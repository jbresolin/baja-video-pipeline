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
"""

import math
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw

from worker import overlay_tokens as theme
from worker.overlay_text import ELLIPSIS, TextRun, font, runs_width, truncate_runs

FINAL_FRAME_HEIGHT = 1080
# Font sizes tried, as fractions of the theme's size, before cutting the text.
SHRINK_STEPS = (1.0, 0.95, 0.9, 0.85, 0.8, 0.75)
# Shapes are drawn this many times larger and scaled down, for smooth corners.
SUPERSAMPLE = 4
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


@dataclass(frozen=True)
class PillLayout:
    """One pill laid out: its size and what to draw inside (x measured from its left edge)."""

    width: float
    height: float
    runs: list[TextRun]
    text_left: float
    font_px: float
    chip_text: str | None = None
    chip_width: float = 0.0


def render_overlay_png(
    destination: Path, text: OverlayText, frame_width: int, frame_height: int = FINAL_FRAME_HEIGHT
) -> Path:
    """Draw the overlay for `text` on a transparent frame-sized canvas and save it as PNG."""
    name = text.name()
    name_runs = [TextRun(name, tracking_em=theme.NAME_TRACKING_EM)] if name else []
    canvas = render_lower_third(
        frame_width,
        frame_height,
        chip_text=str(text.car_number) if text.car_number is not None else None,
        name_runs=name_runs,
        role_runs=role_runs(text.role_parts()),
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
    bottom = frame_height - theme.BOTTOM_OFFSET_PX * scale
    for pill in reversed(pills):
        top = bottom - pill.height
        _draw_pill(canvas, pill, left, top, scale)
        bottom = top - theme.PILL_GAP_PX * scale
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
    runs, font_px = _fit(runs, theme.NAME_FONT_PX * scale, available, scale)
    width = text_left + runs_width(runs, font_px, scale) + padding
    return PillLayout(width, height, runs, text_left, font_px, chip_text, chip_width)


def layout_role_pill(runs: list[TextRun], max_width: float, scale: float) -> PillLayout:
    height = theme.ROLE_PILL_HEIGHT_PX * scale
    padding = theme.ROLE_PILL_PADDING_PX * scale
    runs, font_px = _fit(runs, theme.ROLE_FONT_PX * scale, max_width - 2 * padding, scale)
    return PillLayout(
        2 * padding + runs_width(runs, font_px, scale), height, runs, padding, font_px
    )


def _fit(
    runs: list[TextRun], full_px: float, available: float, scale: float
) -> tuple[list[TextRun], float]:
    """Shrink the font step by step until the runs fit; at 75 % cut them with an ellipsis."""
    for step in SHRINK_STEPS:
        font_px = full_px * step
        if runs_width(runs, font_px, scale) <= available:
            return runs, font_px
    font_px = full_px * SHRINK_STEPS[-1]
    return truncate_runs(runs, font_px, available, scale, ELLIPSIS), font_px


def _draw_pill(
    canvas: Image.Image, pill: PillLayout, left: float, top: float, scale: float
) -> None:
    radius = theme.PILL_RADIUS_PX * scale
    _paste_rounded_box(canvas, left, top, pill.width, pill.height, radius, theme.PANEL_COLOUR)
    centre_y = top + pill.height / 2
    if pill.chip_text is not None:
        _draw_chip(canvas, pill, left, top, centre_y, scale)
    _draw_runs(canvas, pill.runs, left + pill.text_left, centre_y, pill.font_px, scale)


def _draw_chip(
    canvas: Image.Image, pill: PillLayout, left: float, top: float, centre_y: float, scale: float
) -> None:
    inset = theme.CHIP_INSET_PX * scale
    chip_left = left + inset
    radius = theme.CHIP_RADIUS_PX * scale
    chip_height = pill.height - 2 * inset
    _paste_rounded_box(
        canvas, chip_left, top + inset, pill.chip_width, chip_height, radius, theme.CHIP_BACKGROUND
    )
    chip_font = font(theme.CHIP_FONT_PX * scale)
    digits_width = chip_font.getlength(pill.chip_text)
    # The face's digits sit high: like the theme's padding-top of
    # --glyph-nudge, the centred line moves down by half the nudge.
    baseline = _baseline_for_centre(chip_font, centre_y + theme.GLYPH_NUDGE_PX * scale / 2)
    ImageDraw.Draw(canvas).text(
        (chip_left + (pill.chip_width - digits_width) / 2, baseline),
        pill.chip_text,
        font=chip_font,
        fill=theme.CHIP_TEXT_COLOUR,
        anchor="ls",
    )


def _draw_runs(
    canvas: Image.Image,
    runs: list[TextRun],
    x: float,
    centre_y: float,
    font_px: float,
    scale: float,
) -> None:
    if not runs:
        return
    text_font = font(font_px)
    baseline = _baseline_for_centre(text_font, centre_y)
    # Text goes on its own layer and is composited, so its anti-aliased
    # edges blend with the translucent pill instead of replacing it.
    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    gap = theme.ROLE_PART_GAP_PX * scale
    for run in runs:
        if run.gap_before:
            x += gap
        colour = theme.MUTED_TEXT_COLOUR if run.muted else theme.TEXT_COLOUR
        spacing = run.tracking_em * font_px
        for index, character in enumerate(run.text):
            # Measuring the prefix keeps the font's kerning; letter-spacing is
            # added after every character, as browsers do.
            offset = text_font.getlength(run.text[:index]) + index * spacing
            draw.text((x + offset, baseline), character, font=text_font, fill=colour, anchor="ls")
        x += text_font.getlength(run.text) + len(run.text) * spacing
        if run.gap_after:
            x += gap
    canvas.alpha_composite(layer)


def _baseline_for_centre(text_font, centre_y: float) -> float:
    """Where the baseline goes so the font's line box is centred on centre_y (CSS flex centring)."""
    ascent, descent = text_font.getmetrics()
    # Rounded half up to a whole pixel, as the browser snaps the baseline.
    return math.floor(centre_y - (ascent + descent) / 2 + ascent + 0.5)


def _paste_rounded_box(
    canvas: Image.Image,
    left: float,
    top: float,
    width: float,
    height: float,
    radius: float,
    fill: tuple[int, int, int, int],
) -> None:
    """A rounded rectangle with smooth corners, composited onto the canvas."""
    box_left, box_top = round(left), round(top)
    box_width = round(left + width) - box_left
    box_height = round(top + height) - box_top
    if box_width <= 0 or box_height <= 0:
        return
    big = Image.new("RGBA", (box_width * SUPERSAMPLE, box_height * SUPERSAMPLE), (0, 0, 0, 0))
    ImageDraw.Draw(big).rounded_rectangle(
        (0, 0, big.width - 1, big.height - 1), radius=radius * SUPERSAMPLE, fill=fill
    )
    box = big.resize((box_width, box_height), Image.Resampling.LANCZOS)
    canvas.alpha_composite(box, dest=(box_left, box_top))
