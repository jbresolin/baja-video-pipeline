"""Tests for the ffmpeg command builders in shared.ffmpeg.

These never run ffmpeg. They look at the argument list a builder returns
and check the flags that matter are there, with the right values. An
ffmpeg command line is a list like ["ffmpeg", "-i", "in.mp4", ...], and
many flags come as a pair: the flag, then its value right after it.
"""

from pathlib import Path

import pytest

from shared.ffmpeg import (
    NVENC_ENCODER,
    X264_ENCODER,
    ProxySettings,
    build_final_render_command,
    build_probe_command,
    build_proxy_command,
    build_test_clip_command,
    build_thumbnail_command,
    parse_probe_output,
)

RAW = Path("raw.mov")
OUT = Path("out.mp4")


def value_after(args: list[str], flag: str) -> str:
    """Return the item that follows `flag` in the list, e.g. value_after(args, "-c:v")."""
    return args[args.index(flag) + 1]


# --- Proxy ---------------------------------------------------------------


def test_proxy_is_480p_constant_frame_rate_and_faststart():
    args = build_proxy_command(RAW, OUT, X264_ENCODER)

    # Input comes right after -i, and the output path is always last.
    assert value_after(args, "-i") == "raw.mov"
    assert args[-1] == "out.mp4"
    # The video filter scales to 480 lines high (width follows the aspect
    # ratio, rounded to even), forces 30 fps, and uses a browser-safe pixel
    # format.
    assert value_after(args, "-vf") == "scale=-2:480,fps=30,format=yuv420p"
    # "cfr" = constant frame rate, so a time in the proxy equals the same
    # time in the raw. The reviewer's cut marks depend on this.
    assert value_after(args, "-fps_mode") == "cfr"
    # ~1.5 Mbps target.
    assert value_after(args, "-b:v") == "1500k"
    assert value_after(args, "-maxrate") == "1500k"
    # The MP4 index goes at the start so browsers can play before the end
    # of the file has downloaded.
    assert value_after(args, "-movflags") == "+faststart"


def test_proxy_uses_nvenc_when_asked():
    args = build_proxy_command(RAW, OUT, NVENC_ENCODER)
    assert value_after(args, "-c:v") == "h264_nvenc"
    # NVENC presets are named p1..p7, unlike x264's "fast" etc.
    assert value_after(args, "-preset") == "p4"


def test_proxy_falls_back_to_libx264_preset_fast():
    # Design §9: without NVENC, use libx264 -preset fast.
    args = build_proxy_command(RAW, OUT, X264_ENCODER)
    assert value_after(args, "-c:v") == "libx264"
    assert value_after(args, "-preset") == "fast"


def test_proxy_settings_can_be_changed():
    # A custom settings object changes the numbers in the command.
    args = build_proxy_command(RAW, OUT, X264_ENCODER, ProxySettings(height=360, frame_rate=25))
    assert value_after(args, "-vf") == "scale=-2:360,fps=25,format=yuv420p"


def test_commands_never_wait_for_keyboard_input():
    # The worker runs as a Windows service with no keyboard. Without
    # -nostdin, ffmpeg can hang waiting for input. -y avoids the
    # "overwrite? [y/N]" question for the same reason.
    for args in (
        build_proxy_command(RAW, OUT, X264_ENCODER),
        build_final_render_command(RAW, OUT, 1.0, 5.0, X264_ENCODER),
        build_thumbnail_command(RAW, Path("thumb.jpg")),
    ):
        assert "-nostdin" in args
        assert "-y" in args


# --- Final render --------------------------------------------------------


def test_final_render_trims_from_cut_in_for_the_cut_length():
    args = build_final_render_command(RAW, OUT, cut_in_s=12.5, cut_out_s=20.0, encoder=X264_ENCODER)
    # -ss before -i seeks in the input to the cut-in point...
    assert args.index("-ss") < args.index("-i")
    assert value_after(args, "-ss") == "12.500"
    # ...and -t keeps exactly (cut_out - cut_in) = 7.5 seconds.
    assert value_after(args, "-t") == "7.500"


def test_final_render_without_overlay_has_one_input():
    args = build_final_render_command(RAW, OUT, 0.0, 10.0, X264_ENCODER)
    assert args.count("-i") == 1
    graph = value_after(args, "-filter_complex")
    assert "overlay" not in graph


def test_final_render_with_overlay_draws_the_png_bottom_left():
    args = build_final_render_command(
        RAW, OUT, 0.0, 10.0, X264_ENCODER, overlay_image=Path("overlay.png")
    )
    # Two inputs now: the raw (input 0) and the overlay picture (input 1).
    inputs = [args[i + 1] for i, item in enumerate(args) if item == "-i"]
    assert inputs == ["raw.mov", "overlay.png"]
    graph = value_after(args, "-filter_complex")
    # y = main_h - overlay_h puts the picture's bottom on the video's bottom.
    assert "[base][1:v]overlay=x=0:y=main_h-overlay_h" in graph


def test_final_render_normalises_loudness_and_resamples_to_48k():
    args = build_final_render_command(RAW, OUT, 0.0, 10.0, X264_ENCODER)
    graph = value_after(args, "-filter_complex")
    assert "loudnorm=I=-16.0:TP=-1.5:LRA=11.0" in graph
    # Both the video and the audio branch of the graph are mapped to the output.
    assert args.count("-map") == 2
    assert value_after(args, "-ar") == "48000"


def test_final_render_without_audio_skips_the_audio_chain():
    # A raw with no audio track would make loudnorm fail, so the builder
    # must leave audio out entirely and say so with -an ("audio: none").
    args = build_final_render_command(RAW, OUT, 0.0, 10.0, X264_ENCODER, has_audio=False)
    assert "loudnorm" not in value_after(args, "-filter_complex")
    assert "-an" in args
    assert args.count("-map") == 1


def test_final_render_encoder_flags():
    nvenc = build_final_render_command(RAW, OUT, 0.0, 1.0, NVENC_ENCODER)
    x264 = build_final_render_command(RAW, OUT, 0.0, 1.0, X264_ENCODER)
    assert value_after(nvenc, "-c:v") == "h264_nvenc"
    assert value_after(nvenc, "-cq") == "21"
    assert value_after(x264, "-c:v") == "libx264"
    assert value_after(x264, "-crf") == "20"
    assert value_after(x264, "-movflags") == "+faststart"


@pytest.mark.parametrize(("cut_in", "cut_out"), [(5.0, 5.0), (6.0, 5.0)])
def test_final_render_refuses_an_empty_or_backwards_cut(cut_in, cut_out):
    # A cut that ends where (or before) it starts would make an empty clip.
    with pytest.raises(ValueError):
        build_final_render_command(RAW, OUT, cut_in, cut_out, X264_ENCODER)


# --- Small helpers -------------------------------------------------------


def test_thumbnail_grabs_a_single_frame():
    args = build_thumbnail_command(RAW, Path("thumb.jpg"), at_seconds=2.0)
    assert value_after(args, "-frames:v") == "1"
    assert value_after(args, "-ss") == "2.000"


def test_test_clip_uses_builtin_generators():
    args = build_test_clip_command(OUT, duration_s=2.0)
    # "lavfi" inputs are generated by ffmpeg itself: no source file needed.
    assert args.count("lavfi") == 2
    assert value_after(args, "-t") == "2.000"


def test_probe_command_asks_for_json():
    args = build_probe_command(RAW)
    assert args[0] == "ffprobe"
    assert value_after(args, "-print_format") == "json"


def test_parse_probe_output_reads_duration_audio_and_size():
    # A trimmed example of what ffprobe prints.
    probe_json = """
    {"streams": [{"codec_type": "video", "width": 1920, "height": 1080},
                 {"codec_type": "audio"}],
     "format": {"duration": "12.345000"}}
    """
    info = parse_probe_output(probe_json)
    assert info.duration_s == pytest.approx(12.345)
    assert info.has_audio is True
    assert (info.width, info.height) == (1920, 1080)


def test_parse_probe_output_without_audio_or_duration():
    info = parse_probe_output('{"streams": [{"codec_type": "video"}], "format": {}}')
    assert info.has_audio is False
    assert info.duration_s is None
