"""The running clock's pill: laid out and drawn here, its digits written by ffmpeg.

The clock sits in the top-right corner of the 16:9 frame (LIVESTACK-53),
inside the same safe margins as the lower third: its right edge as far
from the right as the lower third is from the left, its top at the
theme's title-safe top. The lower third keeps its place bottom-left.

    ┌──────────────┐
    │  66,0 S      │   digits by ffmpeg, the unit (muted) drawn with the pill
    └──────────────┘

It is bigger than the pills' text (CLOCK_SIZE, from the tokens: the
on-air clock's time size in a pill as tall as the on-air strip), and sized
once for the official time, so it never changes width while it runs.
Bebas Neue's digits all have the same width, so the digits, right-aligned
before the unit, never shift either: "9,9" then "10,0", like a stopwatch.

Pillow draws the pill and the unit (a small PNG; the unit never changes);
ffmpeg's drawtext writes the digits frame by frame with the same font file
and text colour, its baseline where Pillow puts the text
(shared.ffmpeg_clock). A frozen clock is the same text at the same place.

compose_clock() draws a clock with Pillow alone, for the golden images
(the look João reviews) and the preview of a layout.
"""

from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw

from shared.ffmpeg_clock import ClockOverlay
from shared.models import RenderClock
from shared.pass_clock import DECIMAL_COMMA, clock_text, units_per_second
from worker import overlay_tokens as theme
from worker.overlay_draw import PlacedImage, baseline_for_centre, even_region, paste_rounded_box
from worker.overlay_text import font

# A thin space between the number and its unit ("66,0 s"). Bebas Neue has
# no thin-space character, and Pillow draws the unit anyway, so it is a gap.
THIN_SPACE_EM = 1 / 6


@dataclass(frozen=True)
class ClockSize:
    """How big the clock is drawn, at 1080 lines (LIVESTACK-53)."""

    font_px: float
    unit_font_px: float
    height_px: float
    padding_px: float


# The clock João reviews in the golden images (tokens: --size-time digits
# in a --strip-height pill, the unit at --size-strip-time, muted).
CLOCK_SIZE = ClockSize(
    font_px=theme.CLOCK_FONT_PX,
    unit_font_px=theme.CLOCK_UNIT_FONT_PX,
    height_px=theme.CLOCK_PILL_HEIGHT_PX,
    padding_px=theme.CLOCK_PILL_PADDING_PX,
)


@dataclass(frozen=True)
class ClockLayout:
    """Where the clock pill, its digits and its unit go in the 1920×1080 frame."""

    left: float
    top: float
    width: float
    height: float
    font_px: float
    # The digits end here (right-aligned), on this baseline.
    text_right: float
    baseline: int
    # The pill's image: its top-left corner in the frame and its size, even pixels.
    region: tuple[int, int, int, int]
    # The unit after the digits ("S"), drawn by Pillow in the pill's image
    # from `unit_left` on the same baseline; "" = none.
    unit_text: str = ""
    unit_left: float = 0.0
    unit_font_px: float = 0.0
    radius: float = 0.0


def clock_digit_count(clock: RenderClock) -> int:
    """Digits before the comma: as many as the official time has (at least one)."""
    whole_seconds = clock.official_units // units_per_second(clock.decimals)
    return len(str(whole_seconds))


def advance(character: str, font_px: float) -> float:
    """A character's width plus the row tracking after it (as the pills draw text)."""
    return font(font_px).getlength(character) + theme.RESULT_TRACKING_EM * font_px


def unit_text(clock: RenderClock) -> str:
    """The unit as drawn: in capitals like every overlay text; "" for none (or not sent)."""
    return (clock.unit or "").strip().upper()


def digits_width(clock: RenderClock, font_px: float) -> float:
    """The official time's characters: the widest the digits ever get."""
    width = clock_digit_count(clock) * advance("0", font_px)
    if clock.decimals:
        width += advance(DECIMAL_COMMA, font_px) + clock.decimals * advance("0", font_px)
    return width


def unit_width(clock: RenderClock, font_px: float, unit_font_px: float) -> float:
    """The thin space and the unit after the digits; 0 without a unit."""
    unit = unit_text(clock)
    if not unit:
        return 0.0
    return THIN_SPACE_EM * font_px + font(unit_font_px).getlength(unit)


def clock_pill_width(clock: RenderClock, scale: float = 1.0, size: ClockSize = CLOCK_SIZE) -> float:
    """The pill's width: its padding, the official time's characters and the unit.

    Fixed for the whole clip, so it never changes while the clock runs.
    """
    font_px = size.font_px * scale
    padding = size.padding_px * scale
    text = digits_width(clock, font_px) + unit_width(clock, font_px, size.unit_font_px * scale)
    return 2 * padding + text


def layout_clock(
    clock: RenderClock, left: float, top: float, scale: float = 1.0, size: ClockSize = CLOCK_SIZE
) -> ClockLayout:
    """The clock pill with its top-left corner at (left, top)."""
    font_px = size.font_px * scale
    unit_font_px = size.unit_font_px * scale
    padding = size.padding_px * scale
    height = size.height_px * scale
    width = clock_pill_width(clock, scale, size)
    # Like a run of the pills' text: the last digit's tracking is space
    # after it, before the unit (or inside the padding).
    digits_end = left + padding + digits_width(clock, font_px)
    text_right = digits_end - theme.RESULT_TRACKING_EM * font_px
    baseline = baseline_for_centre(font(font_px), top + height / 2)
    region = even_region(left, top, width, height)
    unit = unit_text(clock)
    unit_left = digits_end + THIN_SPACE_EM * font_px if unit else 0.0
    return ClockLayout(
        left,
        top,
        width,
        height,
        font_px,
        text_right,
        baseline,
        region,
        unit_text=unit,
        unit_left=unit_left,
        unit_font_px=unit_font_px,
        radius=theme.PILL_RADIUS_PX * scale,
    )


def layout_clock_top_right(
    clock: RenderClock, frame_width: int, scale: float = 1.0, size: ClockSize = CLOCK_SIZE
) -> ClockLayout:
    """The clock pill in the frame's top-right corner (LIVESTACK-53)."""
    width = clock_pill_width(clock, scale, size)
    left = frame_width - theme.CLOCK_RIGHT_PX * scale - width
    return layout_clock(clock, left, theme.CLOCK_TOP_PX * scale, scale, size)


def draw_clock_pill(layout: ClockLayout) -> Image.Image:
    """The pill and its unit (no digits) on a transparent image the size of its region.

    The unit never changes, so it is drawn here once; ffmpeg writes only the digits.
    """
    region_x, region_y, region_width, region_height = layout.region
    image = Image.new("RGBA", (region_width, region_height), (0, 0, 0, 0))
    paste_rounded_box(
        image,
        layout.left - region_x,
        layout.top - region_y,
        layout.width,
        layout.height,
        layout.radius,
        theme.PANEL_COLOUR,
    )
    if layout.unit_text:
        layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
        ImageDraw.Draw(layer).text(
            (layout.unit_left - region_x, layout.baseline - region_y),
            layout.unit_text,
            font=font(layout.unit_font_px),
            fill=theme.MUTED_TEXT_COLOUR,
            anchor="ls",
        )
        image.alpha_composite(layer)
    return image


def ffmpeg_colour(rgba: tuple[int, int, int, int]) -> str:
    """(242, 244, 248, 255) → "0xf2f4f8ff", how drawtext takes a colour with its alpha."""
    return "0x" + "".join(f"{channel:02x}" for channel in rgba)


def write_clock_pill(
    layout: ClockLayout,
    clock: RenderClock,
    cut_in_s: float,
    directory: Path,
    stem: str,
    result: PlacedImage | None = None,
) -> ClockOverlay:
    """Save the pill (and the result pill, shown from the freeze) as PNGs, and say
    how ffmpeg draws them."""
    directory.mkdir(parents=True, exist_ok=True)
    pill_path = directory / f"{stem}.clock.png"
    draw_clock_pill(layout).save(pill_path, format="PNG")
    result_path = None
    if result is not None:
        result_path = directory / f"{stem}.result.png"
        result.image.save(result_path, format="PNG")
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
        result_png=result_path,
        result_position=None if result is None else result.position,
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
