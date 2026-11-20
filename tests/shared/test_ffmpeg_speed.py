"""The top speed under the clock in the final render's ffmpeg command (LIVESTACK-67).

What this module is for: ffmpeg is told what to draw with a "filter
graph", one text of steps. With a speed, one step is added after the
clock (and after the result pill, when there is one): lay the speed's
pill picture on the frame, switched on (`enable`) only from the speed
mark, an expression of the frame's time `t`.

What these tests prove: the speed pill is one more input after the
others; its step comes last, on top, before the pixel format; it is
switched on when the raw time, in whole microseconds, reaches the mark;
a clock without a speed builds exactly the graph it built before.

What they do not cover: the real ffmpeg, frame by frame
(tests/worker/test_speed_real_ffmpeg.py).
"""

from pathlib import Path

import pytest

from shared.ffmpeg import X264_ENCODER, build_final_render_command
from shared.ffmpeg_clock import ClockFilterError, ClockOverlay, clock_filter
from tests.shared.test_ffmpeg_clock import a_clock, value_after

RAW = Path("raw.mov")
OUT = Path("out.mp4")
OVERLAY = Path("overlay.png")


def a_clock_with_speed(**changes) -> ClockOverlay:
    """The test clock (cut in at 10 s) with a speed pill shown from 15.5 s raw."""
    fields = {
        "speed_png": Path("job.speed.png"),
        "speed_position": (1660, 160),
        "speed_mark_s": 15.5,
    }
    fields.update(changes)
    return a_clock(**fields)


def render_command(clock: ClockOverlay) -> list[str]:
    """The final render's command for a 10–20 s cut with the overlay and `clock`."""
    return build_final_render_command(
        RAW, OUT, 10.0, 20.0, X264_ENCODER, overlay_image=OVERLAY, has_audio=False, clock=clock
    )


def test_the_speed_is_shown_from_its_mark_in_whole_microseconds():
    # Arrange: cut-in 10 s, mark 15.5 s raw: 5 500 000 µs into the clip.
    clock = a_clock_with_speed()

    # Act: build the `enable` expression of the speed pill.
    expression = clock.speed_shown_expression()

    # Assert: the frame's time in µs, minus the offset, compared with 0.
    assert expression == "gte((round(t*1000000)-5500000),0)", "shown from the mark on"


def test_without_a_result_the_speed_pill_is_the_fourth_input():
    # Arrange: a clock with a speed and no result line.
    clock = a_clock_with_speed()

    # Act: build the command.
    command = render_command(clock)

    # Assert: raw, overlay, clock pill, speed pill, in that order.
    inputs = [command[index + 1] for index, arg in enumerate(command) if arg == "-i"]
    assert inputs == ["raw.mov", "overlay.png", "job.clock.png", "job.speed.png"]
    graph = value_after(command, "-filter_complex")
    assert "[with_result][3:v]overlay=x=1660:y=160:" in graph, "laid on from input 3"


def test_with_a_result_the_speed_pill_comes_after_it_and_on_top():
    # Arrange: a clock with a result pill and a speed.
    clock = a_clock_with_speed(result_png=Path("job.result.png"), result_position=(96, 946))

    # Act: build the command.
    command = render_command(clock)

    # Assert: the speed is the fifth input, its step after the result's,
    # and the pixel format still comes last.
    inputs = [command[index + 1] for index, arg in enumerate(command) if arg == "-i"]
    assert inputs[-1] == "job.speed.png", "the speed pill must be the last input"
    graph = value_after(command, "-filter_complex")
    result_step = graph.index("[clock][3:v]overlay")
    speed_step = graph.index("[with_result][4:v]overlay")
    assert result_step < speed_step, "the speed is drawn after (on top of) the result"
    assert "enable='gte((round(t*1000000)-5500000),0)',format=yuv420p[video]" in graph


def test_a_clock_without_a_speed_adds_no_speed_step():
    # Arrange: the test clock as every clip before LIVESTACK-67 had it.
    clock = a_clock()

    # Act: build its filter entries.
    graph = clock_filter(clock, source="lower_third", pill_input=2)

    # Assert: the pill and the digits only, no speed step at all.
    assert graph.count("overlay=") == 1, "only the clock's pill is laid on"
    assert "with_result" not in graph, "no speed, no extra step"


def test_a_speed_image_without_its_input_index_is_refused():
    # Arrange: a clock with a speed pill.
    clock = a_clock_with_speed()

    # Act and assert: asking for the graph without saying where the speed input is fails.
    with pytest.raises(ClockFilterError):
        clock_filter(clock, source="lower_third", pill_input=2)
