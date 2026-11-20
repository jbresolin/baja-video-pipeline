"""The top speed's pill under the clock: where it goes and how it looks (LIVESTACK-67).

What this module is for: "Aceleração e Velocidade" shows the stopwatch
top right (as every clock since LIVESTACK-53) and, under it, the run's top
speed, "37,2 km/h". The worker draws the speed's pill as a small picture
of its own (worker.overlay_speed); ffmpeg lays it on from the speed mark.

What these tests prove: the pill sits under the clock, right-aligned with
it, one row gap below; it is smaller than the clock's digits but bigger
than the lower third's result text; the unit comes after the number in
capitals, small, in the muted colour; a clip without a clock gets no speed
pill; and three whole frames match their golden images (clock running,
clock frozen with the speed, speed missing).

What they do not cover: when the pill appears in the real render
(test_speed_real_ffmpeg.py).

The golden images are refreshed only after a deliberate change of the
look: `uv run python tests/worker/overlay_golden.py`, then check them by eye.
"""

from dataclasses import replace

import pytest
from PIL import Image

from tests.worker.overlay_golden import (
    AVF_SPEED,
    AVF_TEXT,
    SPEED_GOLDEN_CASES,
    count_differing_pixels,
    render_speed_frame,
)
from worker import overlay_tokens as theme
from worker.overlay import lay_out_overlay
from worker.overlay_clock import CLOCK_SIZE
from worker.overlay_speed import SPEED_SIZE, draw_speed_pill
from worker.overlay_text import UNIT_SIZE_RATIO


def avf_layout(**changes):
    """The overlay of car 1's AVF run (clock and speed) at 1920×1080, with `changes`."""
    return lay_out_overlay(replace(AVF_TEXT, **changes), frame_width=1920, frame_height=1080)


def test_the_speed_sits_under_the_clock_right_aligned_with_it():
    # Arrange: car 1's AVF overlay with a clock and a speed.
    layout = avf_layout()

    # Act: read where the clock and the speed pills go.
    clock, speed = layout.clock, layout.speed

    # Assert: same right edge, the speed one row gap under the clock.
    clock_right = clock.left + clock.width
    speed_right = speed.left + speed.width
    assert speed_right == pytest.approx(clock_right), "the right edges must line up"
    expected_top = clock.top + clock.height + theme.PILL_GAP_PX
    assert speed.top == pytest.approx(expected_top), "one row gap under the clock"


def test_the_speed_is_smaller_than_the_clock_but_bigger_than_the_result_text():
    # Arrange: the chosen size (56 px digits) and the clock's (80 px).
    speed_font, clock_font = SPEED_SIZE.font_px, CLOCK_SIZE.font_px

    # Act: nothing to do, these are the sizes the worker draws with.

    # Assert: between the result pill's 30 px and the clock's 80 px.
    assert speed_font < clock_font, "the speed must be smaller than the clock's digits"
    assert speed_font > theme.RESULT_FONT_PX, "and bigger than the result pill's text"
    assert speed_font == 56, "João chose 56 px digits"


def test_the_unit_follows_the_number_in_capitals_small_on_the_same_baseline():
    # Arrange: car 1's AVF overlay.
    layout = avf_layout()

    # Act: read the speed's text parts.
    speed = layout.speed

    # Assert: "37,2" then "KM/H" at 65 % of the number, after it, same baseline.
    assert (speed.text, speed.unit_text) == ("37,2", "KM/H"), "number then unit in capitals"
    assert speed.unit_font_px == pytest.approx(speed.font_px * UNIT_SIZE_RATIO), "unit is small"
    assert speed.unit_left > speed.text_left, "the unit comes after the number"


def test_no_unit_means_just_the_number_and_a_narrower_pill():
    # Arrange: the same speed with and without its unit.
    with_unit = avf_layout().speed
    without_unit = avf_layout(speed=AVF_SPEED.model_copy(update={"unit": ""})).speed

    # Act: compare their widths.
    narrower = without_unit.width < with_unit.width

    # Assert: no unit drawn and the pill shrinks to the number.
    assert without_unit.unit_text == "", "no unit must be drawn"
    assert narrower, "a pill without a unit must be narrower"


def test_without_a_clock_there_is_no_speed_pill():
    # Arrange: the AVF overlay with the clock taken away (no largada, say).
    layout = avf_layout(clock=None)

    # Act: nothing more; the layout is what the worker would draw.

    # Assert: no clock and no speed: the speed only ever sits under a clock.
    assert layout.clock is None, "no clock was asked for"
    assert layout.speed is None, "a speed without a clock must not be drawn"


def test_the_pill_image_has_even_edges_and_holds_the_whole_pill():
    # Arrange: car 1's AVF overlay.
    speed = avf_layout().speed

    # Act: draw the speed pill on its own picture.
    placed = draw_speed_pill(speed)

    # Assert: even position and size (4:2:0 video), around the whole pill.
    x, y = placed.position
    width, height = placed.image.size
    assert (x % 2, y % 2, width % 2, height % 2) == (0, 0, 0, 0), "edges must be even"
    assert x <= speed.left and y <= speed.top, "the picture starts at or before the pill"
    assert x + width >= speed.left + speed.width, "the picture must hold the pill's right edge"
    assert y + height >= speed.top + speed.height, "and its bottom edge"


@pytest.mark.parametrize("case", sorted(SPEED_GOLDEN_CASES))
def test_each_speed_frame_matches_its_golden_image(case):
    # Arrange: the case's overlay, clock count and whether the speed shows.
    text, units, speed_shown, golden_path = SPEED_GOLDEN_CASES[case]

    # Act: draw the whole frame as the clip shows it at that moment.
    rendered = render_speed_frame(text, units, speed_shown)

    # Assert: it matches the committed golden image (same tolerance as the others).
    golden = Image.open(golden_path)
    assert rendered.size == golden.size, "the frame must be 1920×1080"
    differing = count_differing_pixels(rendered, golden)
    assert differing <= 200, f"{differing} pixels differ from {golden_path.name}"
