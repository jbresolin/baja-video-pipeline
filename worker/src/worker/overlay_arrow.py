"""The "→" of the result pill, drawn as a shape (LIVESTACK-47).

The result line reads "66 S · 4 CONES → 86 S", but the vendored Bebas
Neue has no glyph for "→" (U+2192): drawn as text it would come out as the
font's empty "missing glyph" box. So the arrow is drawn here as a vector
shape instead: a shaft and a triangular head, in the text colour,
vertically centred on the face's capital height (where its "-" and "·"
sit). Its size follows the font size, so it shrinks with the text.

    ━━━━━━━━━▶      length ARROW_LENGTH_EM, shaft as thick as Bebas' "-"

The proportions are fractions of the font size (em). tokens.css has no
token for an arrow; these were chosen by eye against the face's strokes.
"""

import math

from PIL import Image, ImageDraw, ImageFont

ARROW = "→"
# Whole arrow, tip included, as wide as about one and a half capitals.
ARROW_LENGTH_EM = 0.6
# The shaft is as thick as Bebas Neue's own "-" (0.10 em).
ARROW_SHAFT_EM = 0.10
# The head: how far it reaches along the shaft, and how tall it is.
ARROW_HEAD_LENGTH_EM = 0.26
ARROW_HEAD_HEIGHT_EM = 0.36
# Drawn this many times larger and scaled down, for smooth edges.
SUPERSAMPLE = 4


def arrow_width(font_px: float) -> float:
    """How wide the arrow is at this font size (the gaps around it are not included)."""
    return ARROW_LENGTH_EM * font_px


def arrow_points(x: float, centre_y: float, font_px: float) -> list[tuple[float, float]]:
    """The arrow's outline, clockwise from the shaft's top-left corner, at (x, centre_y)."""
    length = arrow_width(font_px)
    half_shaft = ARROW_SHAFT_EM * font_px / 2
    half_head = ARROW_HEAD_HEIGHT_EM * font_px / 2
    head_start = x + length - ARROW_HEAD_LENGTH_EM * font_px
    return [
        (x, centre_y - half_shaft),
        (head_start, centre_y - half_shaft),
        (head_start, centre_y - half_head),
        (x + length, centre_y),  # the tip
        (head_start, centre_y + half_head),
        (head_start, centre_y + half_shaft),
        (x, centre_y + half_shaft),
    ]


def cap_height(text_font: ImageFont.FreeTypeFont) -> float:
    """Height of the face's capitals above the baseline, in pixels."""
    _, top, _, _ = text_font.getbbox("H", anchor="ls")
    return -top


def draw_arrow(
    layer: Image.Image,
    x: float,
    baseline: float,
    text_font: ImageFont.FreeTypeFont,
    font_px: float,
    colour: tuple[int, int, int, int],
) -> None:
    """Draw the arrow with its left end at x, centred on the capitals of the text on `baseline`."""
    centre_y = baseline - cap_height(text_font) / 2
    points = arrow_points(x, centre_y, font_px)
    # A small box around the arrow, on whole pixels; the shape inside keeps
    # its fractional position (drawn SUPERSAMPLE times larger).
    left = math.floor(min(px for px, _ in points))
    top = math.floor(min(py for _, py in points))
    width = math.ceil(max(px for px, _ in points)) - left + 1
    height = math.ceil(max(py for _, py in points)) - top + 1
    big = Image.new("RGBA", (width * SUPERSAMPLE, height * SUPERSAMPLE), (0, 0, 0, 0))
    big_points = [((px - left) * SUPERSAMPLE, (py - top) * SUPERSAMPLE) for px, py in points]
    ImageDraw.Draw(big).polygon(big_points, fill=colour)
    small = big.resize((width, height), Image.Resampling.LANCZOS)
    layer.alpha_composite(small, dest=(left, top))
