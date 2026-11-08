"""Drawing laid-out pills onto the overlay canvas (worker.overlay lays them out).

A PillLayout says how big a pill is and what goes inside; draw_pill puts
the rounded slate box, the car chip (name pill only) and the text runs on
the frame-sized transparent canvas. A run marked `arrow` is drawn as the
arrow shape (worker.overlay_arrow) instead of glyphs.
"""

import math
from dataclasses import dataclass

from PIL import Image, ImageDraw

from worker import overlay_tokens as theme
from worker.overlay_arrow import arrow_width, draw_arrow
from worker.overlay_text import TextRun, font

# Shapes are drawn this many times larger and scaled down, for smooth corners.
SUPERSAMPLE = 4


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


@dataclass(frozen=True)
class PlacedImage:
    """A part of the overlay drawn on an image of its own, and where its top-left
    corner goes in the frame (the result pill when the clip has a clock)."""

    image: Image.Image
    position: tuple[int, int]


def even_region(left: float, top: float, width: float, height: float) -> tuple[int, int, int, int]:
    """The smallest box with even edges around a shape: (x, y, width, height).

    4:2:0 video keeps colour per 2×2 pixels, so an image laid on with even
    edges lands on whole colour samples, and tests can cut it out exactly.
    """
    x0 = math.floor(left) // 2 * 2
    y0 = math.floor(top) // 2 * 2
    x1 = math.ceil(left + width)
    y1 = math.ceil(top + height)
    return x0, y0, x1 - x0 + (x1 - x0) % 2, y1 - y0 + (y1 - y0) % 2


def draw_pill_alone(
    pill: PillLayout, left: float, top: float, scale: float, frame_size: tuple[int, int]
) -> PlacedImage:
    """The pill drawn on a small transparent image of its own (even edges), with its place."""
    x, y, width, height = even_region(left, top, pill.width, pill.height)
    canvas = Image.new("RGBA", frame_size, (0, 0, 0, 0))
    draw_pill(canvas, pill, left, top, scale)
    return PlacedImage(canvas.crop((x, y, x + width, y + height)), (x, y))


def draw_pill(canvas: Image.Image, pill: PillLayout, left: float, top: float, scale: float) -> None:
    radius = theme.PILL_RADIUS_PX * scale
    paste_rounded_box(canvas, left, top, pill.width, pill.height, radius, theme.PANEL_COLOUR)
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
    paste_rounded_box(
        canvas, chip_left, top + inset, pill.chip_width, chip_height, radius, theme.CHIP_BACKGROUND
    )
    chip_font = font(theme.CHIP_FONT_PX * scale)
    digits_width = chip_font.getlength(pill.chip_text)
    # The face's digits sit high: like the theme's padding-top of
    # --glyph-nudge, the centred line moves down by half the nudge.
    baseline = baseline_for_centre(chip_font, centre_y + theme.GLYPH_NUDGE_PX * scale / 2)
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
    baseline = baseline_for_centre(text_font, centre_y)
    # Text goes on its own layer and is composited, so its anti-aliased
    # edges blend with the translucent pill instead of replacing it.
    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    gap = theme.ROLE_PART_GAP_PX * scale
    for run in runs:
        if run.gap_before:
            x += gap
        colour = theme.MUTED_TEXT_COLOUR if run.muted else theme.TEXT_COLOUR
        if run.arrow:
            # The face has no "→" glyph: draw the shape, then step past it.
            draw_arrow(layer, x, baseline, text_font, font_px, colour)
            x += arrow_width(font_px)
        else:
            spacing = run.tracking_em * font_px
            for index, character in enumerate(run.text):
                # Measuring the prefix keeps the font's kerning; letter-spacing
                # is added after every character, as browsers do.
                offset = text_font.getlength(run.text[:index]) + index * spacing
                draw.text(
                    (x + offset, baseline), character, font=text_font, fill=colour, anchor="ls"
                )
            x += text_font.getlength(run.text) + len(run.text) * spacing
        if run.gap_after:
            x += gap
    canvas.alpha_composite(layer)


def baseline_for_centre(text_font, centre_y: float) -> float:
    """Where the baseline goes so the font's line box is centred on centre_y (CSS flex centring)."""
    ascent, descent = text_font.getmetrics()
    # Rounded half up to a whole pixel, as the browser snaps the baseline.
    return math.floor(centre_y - (ascent + descent) / 2 + ascent + 0.5)


def paste_rounded_box(
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
