"""The overlay PNG burned into every finished clip, in BajaOvrLay's lower-third look.

The clip carries the same "Tarja" the stream shows on air (BajaOvrLay,
theme Ardósia), drawn here with Pillow as one still image, on for the
whole clip. Every colour, size and radius comes from the vendored
tokens.css through worker.overlay_tokens, and the face is the vendored
Bebas Neue. Bottom-left, at 1920x1080:

    ┌──────────────────────────────┐
    │ [42]  EQUIPE EXEMPLO         │   name pill, 64 px: car chip + team
    └──────────────────────────────┘
    ┌──────────────────────────────────────┐
    │ ENDURO · PASSAGEM 3 · UFMG           │   role pill, 40 px, school muted
    └──────────────────────────────────────┘   ← its bottom at y 940
    ┌───────────────────────────────┐
    │ 66 S · 4 CONES → 86 S         │   result pill, 40 px (LIVESTACK-47)
    └───────────────────────────────┘

The result pill (worker.overlay_result) is only drawn when the task
carries a result line. It goes *under* the role pill, so the name and role
pills sit exactly where they always did, with or without a result.

Unknown parts are left out (no team → the chip alone in the name pill;
no school → no school part; show_pass off → no passagem). Bebas Neue has
no lowercase, so every word is set in capitals. A pill grows with its text
up to --lower-third-max-width (and never past the frame's safe width);
beyond it the font shrinks in steps down to 75 %, then the text is cut
with an ellipsis (a still image cannot scroll like the on-air one).

The PNG is the size of the whole output frame and transparent except for
the pills; shared.ffmpeg overlays it at the bottom-left corner, which for
a full-frame image is simply 0,0. All sizes are for 1080 lines and scale
with the frame height.

Laying out is here; measuring and fitting text is worker.overlay_text,
drawing a laid-out pill is worker.overlay_draw.
"""

from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from worker import overlay_tokens as theme
from worker.overlay_draw import PillLayout, draw_pill
from worker.overlay_result import layout_result_pill, result_runs
from worker.overlay_text import TextRun, fit_runs, font, runs_width

FINAL_FRAME_HEIGHT = 1080
ROLE_SEPARATOR = "·"


@dataclass(frozen=True)
class OverlayText:
    """What the overlay says about one clip (from the process task)."""

    prova: str | None
    car_number: int | None
    team_name: str | None
    passagem: int | None
    show_pass: bool
    school_short: str | None = None
    # The passagem's official result, ready to draw (LIVESTACK-47); None or
    # blank = no result pill.
    pass_result: str | None = None

    def name(self) -> str | None:
        """The team as shown in the name pill, or None when unknown."""
        if self.team_name and self.team_name.strip():
            return self.team_name.strip().upper()
        return None

    def role_parts(self) -> list[tuple[str, bool]]:
        """The role pill's parts in order, as (text, muted); unknown parts left out."""
        parts: list[tuple[str, bool]] = []
        if self.prova and self.prova.strip():
            parts.append((self.prova.strip().upper(), False))
        if self.show_pass and self.passagem is not None:
            parts.append((f"PASSAGEM {self.passagem}", False))
        if self.school_short and self.school_short.strip():
            parts.append((self.school_short.strip().upper(), True))
        return parts

    def result_line(self) -> str | None:
        """The result pill's text as sent, or None when there is none to draw."""
        if self.pass_result and self.pass_result.strip():
            return self.pass_result.strip()
        return None


def render_overlay_png(
    destination: Path, text: OverlayText, frame_width: int, frame_height: int = FINAL_FRAME_HEIGHT
) -> Path:
    """Draw the overlay for `text` on a transparent frame-sized canvas and save it as PNG."""
    name = text.name()
    name_runs = [TextRun(name, tracking_em=theme.NAME_TRACKING_EM)] if name else []
    result = text.result_line()
    canvas = render_lower_third(
        frame_width,
        frame_height,
        chip_text=str(text.car_number) if text.car_number is not None else None,
        name_runs=name_runs,
        role_runs=role_runs(text.role_parts()),
        result_runs=result_runs(result) if result else [],
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(destination, format="PNG")
    return destination


def role_runs(parts: list[tuple[str, bool]]) -> list[TextRun]:
    """Role parts with a muted "·" between them, as BajaOvrLay's role pill draws them.

    Like its `.role` and `.team-line`, the main words are tracked
    (--tracking-caption) and the muted part is not.
    """
    runs: list[TextRun] = []
    for index, (words, muted) in enumerate(parts):
        if index:
            runs.append(TextRun(ROLE_SEPARATOR, muted=True, gap_before=True, gap_after=True))
        tracking = 0.0 if muted else theme.ROLE_TRACKING_EM
        runs.append(TextRun(words, muted=muted, tracking_em=tracking))
    return runs


def render_lower_third(
    frame_width: int,
    frame_height: int,
    *,
    chip_text: str | None,
    name_runs: list[TextRun],
    role_runs: list[TextRun],
    result_runs: list[TextRun] | None = None,
) -> Image.Image:
    """The lower third on a transparent frame-sized RGBA image (the general form).

    render_overlay_png builds the runs from a job; tests also call this with
    BajaOvrLay's own preview data to compare against its screenshots.
    """
    scale = frame_height / FINAL_FRAME_HEIGHT
    left = theme.LEFT_PX * scale
    # The widest a pill may be: the theme's maximum, and never past the
    # frame's safe area (a vertical video is only ~608 px wide).
    max_width = min(theme.MAX_PILL_WIDTH_PX * scale, frame_width - 2 * left)

    pills: list[PillLayout] = []
    if chip_text is not None or name_runs:
        pills.append(layout_name_pill(chip_text, name_runs, max_width, scale))
    if role_runs:
        pills.append(layout_role_pill(role_runs, max_width, scale))

    canvas = Image.new("RGBA", (frame_width, frame_height), (0, 0, 0, 0))
    # Stack from the bottom up: the lowest pill ends at y 940 (scaled).
    lower_third_bottom = frame_height - theme.BOTTOM_OFFSET_PX * scale
    gap = theme.PILL_GAP_PX * scale
    bottom = lower_third_bottom
    for pill in reversed(pills):
        top = bottom - pill.height
        draw_pill(canvas, pill, left, top, scale)
        bottom = top - gap
    if result_runs:
        # Under the role pill, one gap below y 940: the pills above never move.
        result_pill = layout_result_pill(result_runs, max_width, scale)
        draw_pill(canvas, result_pill, left, lower_third_bottom + gap, scale)
    return canvas


def layout_name_pill(
    chip_text: str | None, runs: list[TextRun], max_width: float, scale: float
) -> PillLayout:
    height = theme.NAME_PILL_HEIGHT_PX * scale
    padding = theme.NAME_PILL_PADDING_PX * scale
    inset = theme.CHIP_INSET_PX * scale
    chip_width = 0.0
    text_left = padding
    if chip_text is not None:
        digits = font(theme.CHIP_FONT_PX * scale).getlength(chip_text)
        # At least square, wider for long numbers.
        chip_width = max(height - 2 * inset, digits + 2 * theme.CHIP_TEXT_PADDING_PX * scale)
        if not runs:
            # The chip alone: the same inset on its right as on its left.
            return PillLayout(chip_width + 2 * inset, height, [], 0.0, 0.0, chip_text, chip_width)
        text_left = inset + chip_width + theme.CHIP_NAME_GAP_PX * scale
    available = max_width - text_left - padding
    runs, font_px = fit_runs(runs, theme.NAME_FONT_PX * scale, available, scale)
    width = text_left + runs_width(runs, font_px, scale) + padding
    return PillLayout(width, height, runs, text_left, font_px, chip_text, chip_width)


def layout_role_pill(runs: list[TextRun], max_width: float, scale: float) -> PillLayout:
    height = theme.ROLE_PILL_HEIGHT_PX * scale
    padding = theme.ROLE_PILL_PADDING_PX * scale
    runs, font_px = fit_runs(runs, theme.ROLE_FONT_PX * scale, max_width - 2 * padding, scale)
    return PillLayout(
        2 * padding + runs_width(runs, font_px, scale), height, runs, padding, font_px
    )
