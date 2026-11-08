"""The running clock's pill: where it goes and how it is drawn (LIVESTACK-48).

Background for a beginner: the clip's overlay is one still picture (the
pills with the team, the prova and the result). The clock cannot be in it,
because it changes every frame and only appears at the largada. So the
worker draws the clock's pill as a small picture of its own, and ffmpeg
writes the digits on it frame by frame with the same font and colour.

These tests check the layout (next to the result pill, a fixed width,
even edges as 4:2:0 video needs), what the worker hands ffmpeg, and
golden images of whole frames at the start, mid-run and frozen. The
frame-by-frame timing is tests/worker/test_clock_real_ffmpeg.py.
"""

from dataclasses import replace

import pytest
from PIL import Image

from shared.models import RenderClock
from tests.worker.overlay_golden import (
    CLOCK_66,
    CLOCK_GOLDEN_CASES,
    CLOCK_TEXT,
    RESULT_LONG,
    count_differing_pixels,
    render_clock_frame,
)
from worker import overlay_tokens as theme
from worker.overlay import lay_out_overlay
from worker.overlay_clock import (
    advance,
    clock_pill_width,
    compose_clock,
    draw_clock_pill,
    write_clock_pill,
)
from worker.overlay_result import layout_result_pill, result_runs

# The bottom of the role pill (y 940) plus one gap: the result row's top.
RESULT_ROW_TOP = 1080 - theme.BOTTOM_OFFSET_PX + theme.PILL_GAP_PX


def clock_layout(text=CLOCK_TEXT):
    return lay_out_overlay(text, frame_width=1920).clock


# --- Where it goes ---


def test_the_clock_sits_right_of_the_result_pill_on_its_row():
    layout = clock_layout()
    result = layout_result_pill(result_runs(CLOCK_TEXT.pass_result), 1300, 1.0)
    # Same row as the result pill, one gap (--row-gap) to its right.
    assert layout.top == RESULT_ROW_TOP
    assert layout.left == pytest.approx(theme.LEFT_PX + result.width + theme.PILL_GAP_PX)
    # As tall as the result pill: a row of the theme's scoreboard.
    assert layout.height == theme.RESULT_PILL_HEIGHT_PX


def test_without_a_result_the_clock_starts_at_the_left_edge():
    layout = clock_layout(replace(CLOCK_TEXT, pass_result=None))
    assert (layout.left, layout.top) == (theme.LEFT_PX, RESULT_ROW_TOP)


def test_no_clock_means_no_layout_and_the_still_png_never_has_it():
    # The still PNG is the same with or without a clock: the clock is not
    # in it (it must be absent before the largada), only its place is kept.
    without = lay_out_overlay(replace(CLOCK_TEXT, clock=None, pass_result=None), frame_width=1920)
    with_clock = lay_out_overlay(replace(CLOCK_TEXT, pass_result=None), frame_width=1920)
    assert without.clock is None
    assert with_clock.clock is not None
    assert count_differing_pixels(without.still, with_clock.still) == 0


def test_a_long_result_line_leaves_room_for_the_clock():
    # The long line nearly fills the 1300 px row alone; with a clock, the
    # result pill shrinks so the clock still ends inside the row.
    text = replace(CLOCK_TEXT, pass_result=RESULT_LONG)
    layout = clock_layout(text)
    assert layout.left + layout.width <= theme.LEFT_PX + theme.MAX_PILL_WIDTH_PX + 0.01


# --- Its size ---


def test_the_pill_is_as_wide_as_the_official_time_and_never_changes():
    # 66,0: two digits, a comma, one digit; Bebas Neue's digits all have the
    # same width, so "0,0" and "66,0" fit the same pill.
    digit = advance("0", theme.RESULT_FONT_PX)
    comma = advance(",", theme.RESULT_FONT_PX)
    expected = 2 * theme.RESULT_PILL_PADDING_PX + 3 * digit + comma
    assert clock_pill_width(CLOCK_66) == pytest.approx(expected)
    # A clock that never reaches 10 s has one whole-second digit fewer.
    short = RenderClock(largada_s=0, official_units=95, decimals=1)
    assert clock_pill_width(short) == pytest.approx(expected - digit)
    # Whole seconds: no comma at all.
    whole = RenderClock(largada_s=0, official_units=66, decimals=0)
    assert clock_pill_width(whole) == pytest.approx(2 * theme.RESULT_PILL_PADDING_PX + 2 * digit)


def test_the_region_has_even_edges_and_holds_the_whole_pill():
    layout = clock_layout()
    x, y, width, height = layout.region
    # 4:2:0 video stores colour per 2×2 pixels: crops must be even.
    assert (x % 2, y % 2, width % 2, height % 2) == (0, 0, 0, 0)
    assert x <= layout.left and layout.left + layout.width <= x + width
    assert y <= layout.top and layout.top + layout.height <= y + height


# --- What ffmpeg gets ---


def test_the_digits_end_inside_the_pill_on_the_pills_baseline():
    layout = clock_layout()
    # Right-aligned: the text ends one padding (and the last letter's
    # tracking) before the pill's right edge, like the other pills' text.
    padding = theme.RESULT_PILL_PADDING_PX + theme.RESULT_TRACKING_EM * theme.RESULT_FONT_PX
    assert layout.text_right == pytest.approx(layout.left + layout.width - padding)
    # The baseline is inside the pill, below its middle (the digits sit on it).
    assert layout.top + layout.height / 2 < layout.baseline < layout.top + layout.height


def test_the_pill_image_is_the_pill_alone():
    layout = clock_layout()
    pill = draw_clock_pill(layout)
    assert pill.size == layout.region[2:]
    # The middle of the pill is the slate panel, with no text drawn by Pillow.
    middle = pill.getpixel((pill.width // 2, pill.height // 2))
    # (Within a level or two: the smooth corners are drawn large and scaled down.)
    assert all(abs(a - b) <= 2 for a, b in zip(middle, theme.PANEL_COLOUR, strict=True))


def test_the_worker_hands_ffmpeg_the_pill_font_colour_and_times(tmp_path):
    layout = clock_layout()
    overlay = write_clock_pill(layout, CLOCK_66, cut_in_s=10.0, directory=tmp_path, stem="job")
    assert overlay.pill_png == tmp_path / "job.clock.png"
    assert overlay.pill_png.exists()
    assert overlay.pill_position == layout.region[:2]
    # The pills' own font file and text colour, at the result pill's size.
    assert str(overlay.font_path) == theme.font_path()
    assert overlay.font_px == theme.RESULT_FONT_PX
    red, green, blue, alpha = theme.TEXT_COLOUR
    assert overlay.text_colour == f"0x{red:02x}{green:02x}{blue:02x}{alpha:02x}"
    assert (overlay.text_right, overlay.baseline) == (layout.text_right, layout.baseline)
    assert (overlay.cut_in_s, overlay.largada_s) == (10.0, 12.345)
    assert (overlay.official_units, overlay.decimals) == (660, 1)


def test_the_pill_is_the_same_at_every_count():
    # Only the digits change: the clock does not jump when it freezes.
    layout = clock_layout()
    frozen = compose_clock(layout, 660, 1)
    running = compose_clock(layout, 334, 1)
    # Left of the text (the pill's padding) nothing differs.
    text_left = int(layout.text_right - layout.region[0] - 3 * advance("0", 30) - advance(",", 30))
    for x in range(0, text_left - 1):
        for y in range(frozen.height):
            assert frozen.getpixel((x, y)) == running.getpixel((x, y))


# --- Golden frames: João checks these by eye ---


@pytest.mark.parametrize("case", sorted(CLOCK_GOLDEN_CASES))
def test_each_clock_frame_matches_its_golden_image(case):
    # The whole overlay of car 26's second run with the clock at the
    # largada (0,0), mid-run (33,4), frozen (66,0), and alone (no result).
    text, units, golden_path = CLOCK_GOLDEN_CASES[case]

    rendered = render_clock_frame(text, units)

    golden = Image.open(golden_path)
    assert rendered.size == golden.size
    # Same tolerance as the other golden tests.
    assert count_differing_pixels(rendered, golden) <= 200
