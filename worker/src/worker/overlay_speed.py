"""The top speed's pill under the clock: laid out and drawn here, laid on by ffmpeg (LIVESTACK-67).

"Aceleração e Velocidade" shows the acceleration stopwatch top right, as
every clock (worker.overlay_clock), and under it the run's top speed,
from the frame where the car crosses the speed trap to the end of the clip:

    ┌──────────────┐
    │  4,32 s      │   the clock (80 px digits)
    └──────────────┘
          ┌──────────────┐
          │ 37,2 km/h    │   the speed: right-aligned with the clock,
          └──────────────┘   one row gap below it, smaller

Same family and colours as the clock: the number in the text colour, the
unit a capital at UNIT_SIZE_RATIO of the number in the muted colour after
a thin space (the face has only capitals, so "KM/H" small reads like the
clock's small "S"). The pill is the theme's on-air clock pill grown to
SPEED_SIZE, like the clock's own, so both keep the same proportions.

The speed never changes during the clip, so the whole pill (number and
unit) is one small PNG drawn here; ffmpeg lays it on from the speed mark
(shared.ffmpeg_clock, `enable` on the frame's time).
"""

from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw

from shared.models import RenderSpeed
from worker import overlay_tokens as theme
from worker.overlay_clock import THIN_SPACE_EM, ClockLayout, ClockSize, clock_size
from worker.overlay_draw import PlacedImage, baseline_for_centre, even_region, paste_rounded_box
from worker.overlay_text import font

# João's choice on the golden images (2026-11-20, PR #29): 56 px digits,
# 0.7 of the clock's 80 px and the theme's own on-air clock size, in its
# 72 px pill; the unit 36 px. Changing it is this line and --speed-size in
# review_ui/static/css/overlay_preview.css.
SPEED_SIZE = clock_size(56)


@dataclass(frozen=True)
class SpeedLayout:
    """Where the speed pill, its number and its unit go in the 1920×1080 frame."""

    left: float
    top: float
    width: float
    height: float
    radius: float
    # The number ("37,2") from `text_left`, on `baseline`, at `font_px`.
    text: str
    text_left: float
    baseline: float
    font_px: float
    # The unit after it ("KM/H"), from `unit_left` on the same baseline; "" = none.
    unit_text: str
    unit_left: float
    unit_font_px: float
    # The pill's image: its top-left corner in the frame and its size, even pixels.
    region: tuple[int, int, int, int]


def speed_unit_text(speed: RenderSpeed) -> str:
    """The unit as drawn: in capitals like every overlay text; "" for none."""
    return speed.unit.strip().upper()


def layout_speed_under_clock(
    speed: RenderSpeed, clock: ClockLayout, scale: float = 1.0, size: ClockSize = SPEED_SIZE
) -> SpeedLayout:
    """The speed pill one row gap under the clock pill, their right edges aligned."""
    font_px = size.font_px * scale
    unit_font_px = size.unit_font_px * scale
    padding = size.padding_px * scale
    height = size.height_px * scale
    number_width = font(font_px).getlength(speed.text)
    unit = speed_unit_text(speed)
    unit_width = THIN_SPACE_EM * font_px + font(unit_font_px).getlength(unit) if unit else 0.0
    width = 2 * padding + number_width + unit_width
    clock_right = clock.left + clock.width
    left = clock_right - width
    top = clock.top + clock.height + theme.PILL_GAP_PX * scale
    text_left = left + padding
    return SpeedLayout(
        left=left,
        top=top,
        width=width,
        height=height,
        radius=size.radius_px * scale,
        text=speed.text,
        text_left=text_left,
        baseline=baseline_for_centre(font(font_px), top + height / 2),
        font_px=font_px,
        unit_text=unit,
        unit_left=text_left + number_width + THIN_SPACE_EM * font_px if unit else 0.0,
        unit_font_px=unit_font_px,
        region=even_region(left, top, width, height),
    )


def draw_speed_pill(layout: SpeedLayout) -> PlacedImage:
    """The whole speed pill (box, number, unit) on a transparent image of its region."""
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
    # Text on its own layer, composited, as the other pills draw theirs.
    layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    draw.text(
        (layout.text_left - region_x, layout.baseline - region_y),
        layout.text,
        font=font(layout.font_px),
        fill=theme.TEXT_COLOUR,
        anchor="ls",
    )
    if layout.unit_text:
        draw.text(
            (layout.unit_left - region_x, layout.baseline - region_y),
            layout.unit_text,
            font=font(layout.unit_font_px),
            fill=theme.MUTED_TEXT_COLOUR,
            anchor="ls",
        )
    image.alpha_composite(layer)
    return PlacedImage(image, (region_x, region_y))


def write_speed_pill(layout: SpeedLayout, directory: Path, stem: str) -> tuple[Path, PlacedImage]:
    """Save the speed pill as `<stem>.speed.png` for ffmpeg; the path and the placed image."""
    directory.mkdir(parents=True, exist_ok=True)
    placed = draw_speed_pill(layout)
    path = directory / f"{stem}.speed.png"
    placed.image.save(path, format="PNG")
    return path, placed
