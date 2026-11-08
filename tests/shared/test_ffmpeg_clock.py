"""The running clock in the final render's ffmpeg command (LIVESTACK-48).

Background for a beginner: ffmpeg is told what to draw with a "filter
graph", one text of steps separated by ";" and ",". The clock adds two
steps after the still overlay, both switched on only from the largada
(`enable`): lay the clock's pill PNG on the frame, then write the digits
with `drawtext`, whose text is an expression of the frame's time `t`.
These tests read that text; the frame-by-frame proof with the real ffmpeg
is tests/worker/test_clock_real_ffmpeg.py.

The most important test is the first: a render without a clock must be
exactly the command it was before, so clips without a clock (and their
golden tests) do not change at all.
"""

from pathlib import Path, PureWindowsPath

import pytest

from shared.ffmpeg import X264_ENCODER, build_final_render_command
from shared.ffmpeg_clock import (
    ClockFilterError,
    ClockOverlay,
    clock_filter,
    drawtext_filter,
    quoted_option,
)

RAW = Path("raw.mov")
OUT = Path("out.mp4")
OVERLAY = Path("overlay.png")

# The horizontal render with the overlay, before LIVESTACK-48: must never change.
OLD_GRAPH_WITH_OVERLAY = (
    "[0:v]scale=-2:1080[base];"
    "[base][1:v]overlay=x=0:y=main_h-overlay_h,format=yuv420p[video];"
    "[0:a:0]loudnorm=I=-16.0:TP=-1.5:LRA=11.0[audio]"
)


def a_clock(**changes) -> ClockOverlay:
    """A clock that reaches 66,0 (one decimal), largada 12.345 s, the clip cut in at 10 s."""
    fields = {
        "pill_png": Path("job.clock.png"),
        "pill_position": (430, 942),
        "font_path": Path("/opt/worker/BebasNeue-Regular.ttf"),
        "font_px": 30,
        "text_colour": "0xf2f4f8ff",
        "text_right": 491.25,
        "baseline": 973,
        "cut_in_s": 10.0,
        "largada_s": 12.345,
        "official_units": 660,
        "decimals": 1,
    }
    fields.update(changes)
    return ClockOverlay(**fields)


def value_after(args: list[str], flag: str) -> str:
    return args[args.index(flag) + 1]


def test_without_a_clock_the_command_is_exactly_the_old_one():
    args = build_final_render_command(RAW, OUT, 1.0, 5.0, X264_ENCODER, overlay_image=OVERLAY)
    assert value_after(args, "-filter_complex") == OLD_GRAPH_WITH_OVERLAY
    # Two inputs only: the raw and the overlay PNG.
    assert args.count("-i") == 2


def test_the_clock_adds_its_pill_as_an_input_after_the_overlay():
    args = build_final_render_command(
        RAW, OUT, 10.0, 20.0, X264_ENCODER, overlay_image=OVERLAY, clock=a_clock()
    )
    inputs = [args[index + 1] for index, arg in enumerate(args) if arg == "-i"]
    assert inputs == ["raw.mov", "overlay.png", "job.clock.png"]


def test_the_clock_is_drawn_on_top_of_the_overlay_and_the_format_comes_last():
    args = build_final_render_command(
        RAW, OUT, 10.0, 20.0, X264_ENCODER, overlay_image=OVERLAY, clock=a_clock()
    )
    steps = value_after(args, "-filter_complex").split(";")
    assert steps[0] == "[0:v]scale=-2:1080[base]"
    # The still overlay first, unchanged, then the clock on what it made.
    assert steps[1] == "[base][1:v]overlay=x=0:y=main_h-overlay_h[lower_third]"
    # The pill (input 2) at its place, only from the largada on...
    assert steps[2].startswith("[lower_third][2:v]overlay=x=430:y=942:enable='gte(")
    # ...then the digits, then the pixel format the encoder needs, as before.
    assert ",drawtext=" in steps[2]
    assert steps[2].endswith(",format=yuv420p[video]")
    # Sound is untouched.
    assert steps[3] == "[0:a:0]loudnorm=I=-16.0:TP=-1.5:LRA=11.0[audio]"


def test_the_clock_needs_the_overlay_image():
    with pytest.raises(ValueError):
        build_final_render_command(RAW, OUT, 10.0, 20.0, X264_ENCODER, clock=a_clock())


def test_the_elapsed_time_is_the_frame_time_plus_the_cut_in_minus_the_largada():
    # t = 0 is the cut-in (10 s raw); the largada is at 12.345 s raw, so at
    # t the clock has run t − 2.345 s: in microseconds, t·10⁶ − 2 345 000.
    assert a_clock().elapsed_us_expression() == "(round(t*1000000)-2345000)"
    # A cut-in after the largada: the clock is already running at t = 0.
    assert a_clock(cut_in_s=20.0).elapsed_us_expression() == "(round(t*1000000)+7655000)"


def test_the_count_is_rounded_down_and_stops_at_the_official_time():
    # Tenths of a second (×10, ÷10⁶, floor), never more than 660 (66,0).
    assert a_clock().units_expression() == ("min(floor((round(t*1000000)-2345000)*10/1000000),660)")


def test_the_text_is_whole_seconds_a_comma_and_zero_padded_decimals():
    units = a_clock().units_expression()
    assert a_clock().text_expansion() == (
        f"%{{eif:floor({units}/10):d}},%{{eif:mod({units},10):d:1}}"
    )
    # Hundredths: two digits after the comma, "0,05".
    hundredths = a_clock(decimals=2, official_units=6600)
    assert hundredths.text_expansion().endswith(":d:2}")
    # Whole seconds: no comma at all.
    whole = a_clock(decimals=0, official_units=66)
    assert "," not in whole.text_expansion().replace(whole.units_expression(), "")


def test_the_digits_are_right_aligned_on_the_pills_baseline_in_their_font_and_colour():
    drawtext = drawtext_filter(a_clock(), "33,4", None)
    assert drawtext == (
        "drawtext=fontfile='/opt/worker/BebasNeue-Regular.ttf':fontsize=30"
        ":fontcolor=0xf2f4f8ff:x='491.250-text_w':y_align=baseline:y=973:text='33,4'"
    )


def test_colons_and_backslashes_are_escaped_inside_the_quotes():
    # A Windows install path: "C:" must not end the option.
    windows = a_clock(font_path=PureWindowsPath(r"C:\Program Files\Baja Worker\BebasNeue.ttf"))
    assert "fontfile='C\\:/Program Files/Baja Worker/BebasNeue.ttf'" in clock_filter(
        windows, source="s", pill_input=2
    )
    # The text's own ":" (text expansion) are escaped the same way.
    assert "%{eif\\:floor(" in clock_filter(a_clock(), source="s", pill_input=2)
    assert quoted_option("a\\b") == "'a\\\\b'"


def test_a_single_quote_cannot_be_written_and_is_refused():
    with pytest.raises(ClockFilterError):
        quoted_option("D'Ávila")


def test_the_clock_shows_from_the_largada_on():
    assert a_clock().shown_expression() == "gte((round(t*1000000)-2345000),0)"
    entry = clock_filter(a_clock(), source="lower_third", pill_input=2)
    # Both the pill and the digits carry the same switch.
    assert entry.count("enable='gte((round(t*1000000)-2345000),0)'") == 2
