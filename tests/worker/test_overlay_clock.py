"""The running clock's pill: where it goes and how it is drawn (LIVESTACK-48, LIVESTACK-53).

Background for a beginner: the clip's overlay is one still picture (the
pills with the team, the prova and the result). The clock cannot be in it,
because it changes every frame and only appears at the largada. So the
worker draws the clock's pill (and its unit, "S", which never changes) as
a small picture of its own, and ffmpeg writes the digits on it frame by
frame with the same font and colour.

Since LIVESTACK-53 the clock is bigger than the pills' text, reads
"66,0 s", and sits in the top-right corner of the 16:9 frame, mirroring
the lower third: as far from the right edge as the lower third is from
the left, and nearer the top than the lower third is to the bottom.

These tests check the layout (top right, a fixed width, even edges as
4:2:0 video needs), the unit, what the worker hands ffmpeg, and golden
images of whole frames at the start, mid-run and frozen. The
frame-by-frame timing is tests/worker/test_clock_real_ffmpeg.py.
"""

from dataclasses import replace

import pytest
from PIL import Image

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
    CLOCK_SIZE,
    THIN_SPACE_EM,
    advance,
    clock_pill_width,
    compose_clock,
    draw_clock_pill,
    write_clock_pill,
)
from worker.overlay_result import layout_result_pill, result_runs
from worker.overlay_text import font

# The bottom of the role pill (y 940) plus one gap: the result row's top.
RESULT_ROW_TOP = 1080 - theme.BOTTOM_OFFSET_PX + theme.PILL_GAP_PX


def clock_layout(text=CLOCK_TEXT, frame_width=1920, frame_height=1080):
    return lay_out_overlay(text, frame_width=frame_width, frame_height=frame_height).clock


# --- Where it goes ---


def test_the_clock_sits_in_the_top_right_corner_mirroring_the_lower_third():
    layout = clock_layout()
    # Its right edge is as far from the frame's right edge (1920) as the
    # lower third is from the left one (96 px): the two corners mirror.
    assert 1920 - (layout.left + layout.width) == pytest.approx(theme.LEFT_PX)
    assert theme.CLOCK_RIGHT_PX == theme.LEFT_PX == 96
    # Its top at the theme's title-safe top, 54 px (5 % of 1080).
    assert layout.top == theme.CLOCK_TOP_PX == 54


def test_the_top_margin_is_smaller_than_the_lower_thirds_and_inside_action_safe():
    # João: the clock may sit closer to the top edge than the lower third
    # sits to the bottom edge, but inside action-safe (at least ~3.5 %).
    lower_third_bottom_margin = theme.BOTTOM_OFFSET_PX  # the role pill ends at y 940
    assert lower_third_bottom_margin > theme.CLOCK_TOP_PX
    assert theme.CLOCK_TOP_PX >= 0.035 * 1080


def test_the_result_keeps_its_place_and_its_whole_row():
    # The clock left the result's row: the result pill is laid out exactly
    # as without a clock (under the role pill, the full width available).
    with_clock = lay_out_overlay(replace(CLOCK_TEXT, pass_result=RESULT_LONG), frame_width=1920)
    pill = layout_result_pill(result_runs(RESULT_LONG), theme.MAX_PILL_WIDTH_PX, 1.0)
    x, y = with_clock.result.position
    assert (x, y) == (theme.LEFT_PX // 2 * 2, int(RESULT_ROW_TOP) // 2 * 2)
    assert with_clock.result.image.width >= pill.width


def test_no_clock_means_no_layout_and_the_still_png_never_has_it():
    # The still PNG is the same with or without a clock: the clock is not
    # in it (it must be absent before the largada).
    without = lay_out_overlay(replace(CLOCK_TEXT, clock=None, pass_result=None), frame_width=1920)
    with_clock = lay_out_overlay(replace(CLOCK_TEXT, pass_result=None), frame_width=1920)
    assert without.clock is None
    assert with_clock.clock is not None
    assert count_differing_pixels(without.still, with_clock.still) == 0


def test_with_a_clock_the_result_is_not_in_the_still_png():
    # The result appears when the clock stops (LIVESTACK-53): it is drawn
    # on its own picture, and the still one is the one without a result.
    with_both = lay_out_overlay(CLOCK_TEXT, frame_width=1920)
    neither = lay_out_overlay(replace(CLOCK_TEXT, clock=None, pass_result=None), frame_width=1920)
    assert with_both.result is not None
    assert count_differing_pixels(with_both.still, neither.still) == 0


def test_without_a_clock_the_result_stays_in_the_still_png():
    only_result = lay_out_overlay(replace(CLOCK_TEXT, clock=None), frame_width=1920)
    assert only_result.result is None
    neither = lay_out_overlay(replace(CLOCK_TEXT, clock=None, pass_result=None), frame_width=1920)
    assert count_differing_pixels(only_result.still, neither.still) > 0


# --- Its size and its unit ---


def test_the_clock_is_clearly_bigger_than_the_pills_text():
    # The proposal (golden images): the on-air clock's 56 px digits, 1.87 ×
    # the result pill's 30 px, in a 72 px pill.
    assert CLOCK_SIZE.font_px == 56
    assert 1.6 <= CLOCK_SIZE.font_px / theme.RESULT_FONT_PX <= 2
    assert CLOCK_SIZE.height_px == 72
    assert clock_layout().height == 72


def test_the_pill_is_as_wide_as_the_official_time_and_its_unit_and_never_changes():
    # 66,0 s: two digits, a comma, one digit, a thin space and the "S";
    # Bebas Neue's digits all have the same width, so "0,0" and "66,0" fit
    # the same pill.
    digit = advance("0", 56)
    comma = advance(",", 56)
    unit = THIN_SPACE_EM * 56 + font(44).getlength("S")
    padding = 2 * theme.CLOCK_PILL_PADDING_PX
    assert clock_pill_width(CLOCK_66) == pytest.approx(padding + 3 * digit + comma + unit)
    # A clock that never reaches 10 s has one whole-second digit fewer.
    short = CLOCK_66.model_copy(update={"official_units": 95})
    assert clock_pill_width(short) == pytest.approx(padding + 2 * digit + comma + unit)
    # Whole seconds: no comma at all.
    whole = CLOCK_66.model_copy(update={"official_units": 66, "decimals": 0})
    assert clock_pill_width(whole) == pytest.approx(padding + 2 * digit + unit)


def test_no_unit_means_no_thin_space_either():
    # An empty unit in the configuration, or a task from before the unit
    # existed (None): just the digits.
    for unit in ("", None):
        clock = CLOCK_66.model_copy(update={"unit": unit})
        expected = 2 * theme.CLOCK_PILL_PADDING_PX + 3 * advance("0", 56) + advance(",", 56)
        assert clock_pill_width(clock) == pytest.approx(expected)
        assert clock_layout(replace(CLOCK_TEXT, clock=clock)).unit_text == ""


def test_the_unit_follows_the_digits_in_capitals_on_their_baseline():
    layout = clock_layout(replace(CLOCK_TEXT, clock=CLOCK_66.model_copy(update={"unit": "seg"})))
    assert layout.unit_text == "SEG"
    assert layout.unit_font_px == theme.CLOCK_UNIT_FONT_PX == 44
    # A thin space (1/6 of the digits' size) after the last digit's tracking.
    gap = layout.unit_left - layout.text_right
    tracking = theme.RESULT_TRACKING_EM * 56
    assert gap == pytest.approx(tracking + THIN_SPACE_EM * 56)
    # And the unit ends one padding before the pill's right edge.
    unit_right = layout.unit_left + font(44).getlength("SEG")
    right = layout.left + layout.width - theme.CLOCK_PILL_PADDING_PX
    assert unit_right == pytest.approx(right)


def test_the_region_has_even_edges_and_holds_the_whole_pill():
    layout = clock_layout()
    x, y, width, height = layout.region
    # 4:2:0 video stores colour per 2×2 pixels: crops must be even.
    assert (x % 2, y % 2, width % 2, height % 2) == (0, 0, 0, 0)
    assert x <= layout.left and layout.left + layout.width <= x + width
    assert y <= layout.top and layout.top + layout.height <= y + height


def test_on_a_smaller_frame_everything_scales_with_the_height():
    # A 1280×720 frame (the overlay is laid out for any height): two thirds.
    small = clock_layout(frame_width=1280, frame_height=720)
    assert small.height == pytest.approx(48)
    assert small.top == pytest.approx(36)
    assert 1280 - (small.left + small.width) == pytest.approx(64)


# --- What ffmpeg gets ---


def test_the_digits_end_before_the_unit_on_the_pills_baseline():
    layout = clock_layout()
    # The baseline is inside the pill, below its middle (the digits sit on it).
    assert layout.top + layout.height / 2 < layout.baseline < layout.top + layout.height
    # Right-aligned: the digits end before the unit, never under it.
    assert layout.text_right < layout.unit_left


def test_the_pill_image_is_the_pill_and_the_unit_without_digits():
    layout = clock_layout()
    pill = draw_clock_pill(layout)
    assert pill.size == layout.region[2:]
    # Where the digits go, the pill is the slate panel: ffmpeg writes them.
    # (Within a level or two: the smooth corners are drawn large and scaled down.)
    digits_middle = (int(layout.text_right - layout.region[0] - 30), pill.height // 2)
    sample = pill.getpixel(digits_middle)
    assert all(abs(a - b) <= 2 for a, b in zip(sample, theme.PANEL_COLOUR, strict=True))
    # The unit is drawn, in the muted text colour.
    unit_box = (
        int(layout.unit_left - layout.region[0]),
        0,
        int(layout.left + layout.width - layout.region[0]),
        pill.height,
    )
    colours = {colour[:3] for _, colour in pill.crop(unit_box).getcolors(maxcolors=100_000)}
    assert theme.MUTED_TEXT_COLOUR[:3] in colours


def test_the_worker_hands_ffmpeg_the_pill_font_colour_and_times(tmp_path):
    layout = clock_layout()
    overlay = write_clock_pill(layout, CLOCK_66, cut_in_s=10.0, directory=tmp_path, stem="job")
    assert overlay.pill_png == tmp_path / "job.clock.png"
    assert overlay.pill_png.exists()
    assert overlay.pill_position == layout.region[:2]
    # The pills' own font file and text colour, at the clock's size.
    assert str(overlay.font_path) == theme.font_path()
    assert overlay.font_px == 56
    red, green, blue, alpha = theme.TEXT_COLOUR
    assert overlay.text_colour == f"0x{red:02x}{green:02x}{blue:02x}{alpha:02x}"
    assert (overlay.text_right, overlay.baseline) == (layout.text_right, layout.baseline)
    assert (overlay.cut_in_s, overlay.largada_s) == (10.0, 12.345)
    assert (overlay.official_units, overlay.decimals) == (660, 1)
    # No result line given: no result picture.
    assert overlay.result_png is None


def test_the_pill_is_the_same_at_every_count():
    # Only the digits change: the clock does not jump when it freezes.
    layout = clock_layout()
    frozen = compose_clock(layout, 660, 1)
    running = compose_clock(layout, 334, 1)
    # Left of the digits (the pill's padding) and from the unit on, nothing differs.
    digits_left = int(
        layout.text_right - layout.region[0] - 3 * advance("0", 56) - advance(",", 56)
    )
    unit_left = int(layout.unit_left - layout.region[0])
    for x in [*range(0, digits_left - 1), *range(unit_left, frozen.width)]:
        for y in range(frozen.height):
            assert frozen.getpixel((x, y)) == running.getpixel((x, y))


# --- Golden frames: João checks these by eye ---


@pytest.mark.parametrize("case", sorted(CLOCK_GOLDEN_CASES))
def test_each_clock_frame_matches_its_golden_image(case):
    # The whole overlay of car 26's second run with the clock at the
    # largada (0,0) and mid-run (33,4), both without the result; frozen
    # (66,0) with the result; and alone (no result line at all).
    text, units, golden_path = CLOCK_GOLDEN_CASES[case]

    rendered = render_clock_frame(text, units)

    golden = Image.open(golden_path)
    assert rendered.size == golden.size
    # Same tolerance as the other golden tests.
    assert count_differing_pixels(rendered, golden) <= 200
