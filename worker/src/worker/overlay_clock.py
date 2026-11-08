"""The running clock's pill: laid out and drawn here, its digits written by ffmpeg (LIVESTACK-48).

The clock sits on the result pill's row, right of it (one --row-gap
between), or alone at the left edge when the clip has no result pill:

    ┌───────────────────────────────┐ ┌────────┐
    │ 66 S · 4 CONES → 86 S         │ │  12,3  │   result pill, clock pill
    └───────────────────────────────┘ └────────┘

It is a pill like the result pill (--board-row-height, --size-board,
--space-4 each side), sized once for the official time, so it never
changes width while it runs. Bebas Neue's digits all have the same width,
so the digits, right-aligned in it, never shift either: "9,9" then "10,0",
like a stopwatch.

Pillow draws the pill (a small PNG); ffmpeg's drawtext writes the digits
frame by frame with the same font file and text colour, its baseline
where Pillow puts the pills' text (shared.ffmpeg_clock). The clock does
not jump when it freezes: a frozen clock is the same text at the same place.

compose_clock() draws a clock with Pillow alone, for the golden images
(the look João reviews) and the preview of a layout.
"""

import math
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw

from shared.ffmpeg_clock import ClockOverlay
from shared.models import RenderClock
from shared.pass_clock import DECIMAL_COMMA, clock_text, units_per_second
from worker import overlay_tokens as theme
from worker.overlay_draw import baseline_for_centre, paste_rounded_box
from worker.overlay_text import font


@dataclass(frozen=True)
class ClockLayout:
    """Where the clock pill and its text go in the 1920×1080 frame."""

    left: float
    top: float
    width: float
    height: float
    font_px: float
    # The text ends here (right-aligned), on this baseline.
    text_right: float
    baseline: int
    # The pill's image: its top-left corner in the frame and its size, even pixels.
    region: tuple[int, int, int, int]


def clock_digit_count(clock: RenderClock) -> int:
    """Digits before the comma: as many as the official time has (at least one)."""
    whole_seconds = clock.official_units // units_per_second(clock.decimals)
    return len(str(whole_seconds))


def advance(character: str, font_px: float) -> float:
    """A character's width plus the row tracking after it (as the pills draw text)."""
    return font(font_px).getlength(character) + theme.RESULT_TRACKING_EM * font_px


def clock_pill_width(clock: RenderClock, scale: float = 1.0) -> float:
    """The pill's width: its padding and the official time's characters."""
    font_px = theme.RESULT_FONT_PX * scale
    text_width = clock_digit_count(clock) * advance("0", font_px)
    if clock.decimals:
        text_width += advance(DECIMAL_COMMA, font_px) + clock.decimals * advance("0", font_px)
    return 2 * theme.RESULT_PILL_PADDING_PX * scale + text_width


def layout_clock(clock: RenderClock, left: float, top: float, scale: float = 1.0) -> ClockLayout:
    """The clock pill with its top-left corner at (left, top)."""
    font_px = theme.RESULT_FONT_PX * scale
    height = theme.RESULT_PILL_HEIGHT_PX * scale
    width = clock_pill_width(clock, scale)
    # Like a run of the pills' text: the last character's tracking is space
    # after it, inside the padding.
    text_right = left + width - theme.RESULT_PILL_PADDING_PX * scale
    text_right -= theme.RESULT_TRACKING_EM * font_px
    baseline = baseline_for_centre(font(font_px), top + height / 2)
    region = _even_region(left, top, width, height)
    return ClockLayout(left, top, width, height, font_px, text_right, baseline, region)


def _even_region(left: float, top: float, width: float, height: float) -> tuple[int, int, int, int]:
    """The smallest box with even edges around the pill: 4:2:0 video keeps colour
    per 2×2 pixels, so the pill lands on whole colour samples, and tests can cut it out."""
    x0 = math.floor(left) // 2 * 2
    y0 = math.floor(top) // 2 * 2
    x1 = math.ceil(left + width)
    y1 = math.ceil(top + height)
    return x0, y0, x1 - x0 + (x1 - x0) % 2, y1 - y0 + (y1 - y0) % 2


def draw_clock_pill(layout: ClockLayout) -> Image.Image:
    """The pill alone (no text) on a transparent image the size of its region."""
    region_x, region_y, region_width, region_height = layout.region
    image = Image.new("RGBA", (region_width, region_height), (0, 0, 0, 0))
    radius = theme.PILL_RADIUS_PX * layout.height / theme.RESULT_PILL_HEIGHT_PX
    paste_rounded_box(
        image,
        layout.left - region_x,
        layout.top - region_y,
        layout.width,
        layout.height,
        radius,
        theme.PANEL_COLOUR,
    )
    return image


def ffmpeg_colour(rgba: tuple[int, int, int, int]) -> str:
    """(242, 244, 248, 255) → "0xf2f4f8ff", how drawtext takes a colour with its alpha."""
    return "0x" + "".join(f"{channel:02x}" for channel in rgba)


def write_clock_pill(
    layout: ClockLayout, clock: RenderClock, cut_in_s: float, directory: Path, stem: str
) -> ClockOverlay:
    """Save the pill as a PNG, and say how ffmpeg draws the clock."""
    directory.mkdir(parents=True, exist_ok=True)
    pill_path = directory / f"{stem}.clock.png"
    draw_clock_pill(layout).save(pill_path, format="PNG")
    return ClockOverlay(
        pill_png=pill_path,
        pill_position=(layout.region[0], layout.region[1]),
        font_path=Path(theme.font_path()),
        font_px=round(layout.font_px),
        text_colour=ffmpeg_colour(theme.TEXT_COLOUR),
        text_right=layout.text_right,
        baseline=layout.baseline,
        cut_in_s=cut_in_s,
        largada_s=clock.largada_s,
        official_units=clock.official_units,
        decimals=clock.decimals,
    )


def compose_clock(layout: ClockLayout, units: int, decimals: int) -> Image.Image:
    """The clock at `units` drawn with Pillow alone, on its region (golden images)."""
    region_x, region_y = layout.region[0], layout.region[1]
    image = draw_clock_pill(layout)
    layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
    ImageDraw.Draw(layer).text(
        (layout.text_right - region_x, layout.baseline - region_y),
        clock_text(units, decimals),
        font=font(layout.font_px),
        fill=theme.TEXT_COLOUR,
        anchor="rs",
    )
    image.alpha_composite(layer)
    return image
