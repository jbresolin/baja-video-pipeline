"""The overlay PNG drawn on every finished clip (contract §30: bottom-left).

ffmpeg places the PNG at x=0, y=bottom (shared.ffmpeg.build_final_render_
command), so the picture carries its own margin: a transparent canvas with
a dark, semi-transparent box near its bottom-left corner holding two lines:

    #42  Equipe Exemplo
    Enduro · Passagem 3        ("· Passagem n" only when show_pass)

Sizes scale with the frame height (the final render is 1080 lines tall),
and the text shrinks until the box fits the frame width, so portrait phone
videos get a legible overlay too.
"""

from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FINAL_FRAME_HEIGHT = 1080
# Sizes at 1080 lines; scaled for other heights.
MARGIN_PX = 40
PADDING_PX = 24
LINE_GAP_PX = 10
TITLE_FONT_PX = 64
SUBTITLE_FONT_PX = 40
SMALLEST_TITLE_FONT_PX = 20
BOX_COLOUR = (0, 0, 0, 170)  # black, about two thirds opaque
TEXT_COLOUR = (255, 255, 255, 255)

# Tried in order; Pillow looks them up in the system font folders. The
# first two exist on Windows, the others on common Linux installs.
_FONT_CANDIDATES = (
    "arialbd.ttf",
    "segoeuib.ttf",
    "DejaVuSans-Bold.ttf",
    "LiberationSans-Bold.ttf",
)


@dataclass(frozen=True)
class OverlayText:
    prova: str
    car_number: int
    team_name: str | None
    passagem: int
    show_pass: bool

    def lines(self) -> tuple[str, str]:
        title = f"#{self.car_number}"
        if self.team_name:
            title += f"  {self.team_name}"
        subtitle = self.prova
        if self.show_pass:
            subtitle += f" · Passagem {self.passagem}"
        return title, subtitle


def final_frame_width(source_width: int | None, source_height: int | None) -> int:
    """Width of the final render: scale=-2:1080 keeps the aspect, even width."""
    if not source_width or not source_height:
        return 1920
    width = round(source_width * FINAL_FRAME_HEIGHT / source_height)
    return width - (width % 2)


def render_overlay_png(
    destination: Path, text: OverlayText, frame_width: int, frame_height: int = FINAL_FRAME_HEIGHT
) -> Path:
    """Draw the overlay and save it as a transparent PNG at `destination`."""
    scale = frame_height / FINAL_FRAME_HEIGHT
    margin, padding, gap = (round(px * scale) for px in (MARGIN_PX, PADDING_PX, LINE_GAP_PX))
    title, subtitle = text.lines()
    widest_allowed = frame_width - 2 * margin - 2 * padding

    # Shrink both lines together until the wider one fits the frame.
    title_px = round(TITLE_FONT_PX * scale)
    while True:
        title_font = _load_font(title_px)
        subtitle_font = _load_font(round(title_px * SUBTITLE_FONT_PX / TITLE_FONT_PX))
        title_box = title_font.getbbox(title)
        subtitle_box = subtitle_font.getbbox(subtitle)
        text_width = max(title_box[2], subtitle_box[2])
        if text_width <= widest_allowed or title_px <= SMALLEST_TITLE_FONT_PX:
            break
        title_px = max(SMALLEST_TITLE_FONT_PX, int(title_px * 0.9))

    title_height = title_box[3]
    subtitle_height = subtitle_box[3]
    box_width = text_width + 2 * padding
    box_height = title_height + gap + subtitle_height + 2 * padding
    canvas = Image.new("RGBA", (box_width + margin, box_height + margin), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    # The box sits `margin` from the left and from the bottom of the canvas.
    box_left, box_top = margin, 0
    draw.rounded_rectangle(
        (box_left, box_top, box_left + box_width, box_top + box_height),
        radius=round(12 * scale),
        fill=BOX_COLOUR,
    )
    draw.text((box_left + padding, box_top + padding), title, font=title_font, fill=TEXT_COLOUR)
    subtitle_top = box_top + padding + title_height + gap
    draw.text((box_left + padding, subtitle_top), subtitle, font=subtitle_font, fill=TEXT_COLOUR)

    destination.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(destination, format="PNG")
    return destination


def _load_font(size_px: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for name in _FONT_CANDIDATES:
        try:
            return ImageFont.truetype(name, size_px)
        except OSError:
            continue
    # Pillow's built-in font; scalable when Pillow has FreeType (the wheels do).
    return ImageFont.load_default(size=size_px)
