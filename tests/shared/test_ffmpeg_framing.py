"""Tests for the final render's picture and sound options (LIVESTACK-50).

Two kinds of test, like the other ffmpeg tests:

* string tests read the `-filter_complex` graph the builder returns and
  check each step (no ffmpeg needed). A filter graph is a list of steps
  separated by ";", each "[input]filter,filter,...[output]";
* real-ffmpeg tests (skipped when ffmpeg is not installed, as in CI)
  render a tiny generated clip with each framing and check what came out:
  always 1920×1080, with sound or without.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from shared.ffmpeg import (
    TONE_MAP_HDR_TO_SDR,
    X264_ENCODER,
    build_final_render_command,
    build_test_clip_command,
)
from shared.framing import plan_frame
from shared.models import Framing

RAW = Path("raw.mov")
OUT = Path("out.mp4")
OVERLAY = Path("overlay.png")

# The old horizontal render, before LIVESTACK-50: must never change.
OLD_GRAPH_WITH_OVERLAY = (
    "[0:v]scale=-2:1080[base];"
    "[base][1:v]overlay=x=0:y=main_h-overlay_h,format=yuv420p[video];"
    "[0:a:0]loudnorm=I=-16.0:TP=-1.5:LRA=11.0[audio]"
)


def value_after(args: list[str], flag: str) -> str:
    return args[args.index(flag) + 1]


def graph(plan, **options) -> str:
    """The -filter_complex text for a render with this plan and the overlay."""
    args = build_final_render_command(
        RAW, OUT, 1.0, 5.0, X264_ENCODER, overlay_image=OVERLAY, frame_plan=plan, **options
    )
    return value_after(args, "-filter_complex")


# --- Horizontal 16:9: exactly as before ---


def test_a_16_9_video_keeps_the_old_graph_with_or_without_a_plan():
    # No plan (an older caller) and a plan for a 16:9 picture give the very
    # same command: horizontal clips look the same to the pixel.
    assert graph(None) == OLD_GRAPH_WITH_OVERLAY
    assert graph(plan_frame(1920, 1080, framing=Framing.BLACK_BARS)) == OLD_GRAPH_WITH_OVERLAY
    assert graph(plan_frame(3840, 2160)) == OLD_GRAPH_WITH_OVERLAY


# --- An upright phone video, each framing ---


def test_blurred_fill_shrinks_blurs_darkens_and_scales_up_a_copy_behind_the_fitted_video():
    steps = graph(plan_frame(1080, 1920, framing=Framing.BLURRED)).split(";")
    # Step 1: the picture is split in two copies.
    assert steps[0] == "[0:v]split=2[fill][front]"
    # Step 2: the background copy is made on a small 480×270 canvas (cheap),
    # covering it and cut to 16:9, blurred, darkened with a 45 % black box,
    # then scaled up to the full frame. Its scaling uses the fast bilinear
    # filters: invisible on a blur, and cheaper than the default bicubic.
    assert steps[1] == (
        "[fill]scale=480:270:force_original_aspect_ratio=increase:flags=fast_bilinear,"
        "crop=480:270,boxblur=12:2,drawbox=color=black@0.45:thickness=fill,"
        "scale=1920:1080:flags=bilinear,setsar=1[blurred]"
    )
    # Step 3: the front copy is the whole video at full height (608×1080)...
    assert steps[2] == "[front]scale=608:1080,setsar=1[fitted]"
    # Step 4: ...laid in the middle of the blurred one ((1920 − 608) / 2 = 656).
    assert steps[3] == "[blurred][fitted]overlay=x=656:y=0:format=auto[base]"
    # Then the overlay goes on top as always, and the sound as always.
    assert steps[4] == "[base][1:v]overlay=x=0:y=main_h-overlay_h,format=yuv420p[video]"
    assert steps[5].startswith("[0:a:0]loudnorm=")


def test_black_bars_pad_the_fitted_video_to_1920x1080():
    steps = graph(plan_frame(1080, 1920, framing=Framing.BLACK_BARS)).split(";")
    assert steps[0] == "[0:v]scale=608:1080,pad=1920:1080:656:0:color=black,setsar=1[base]"


def test_cortar_cuts_the_chosen_band_and_scales_it_to_1920x1080():
    steps = graph(plan_frame(1080, 1920, framing=Framing.CROP, crop_position=0.25)).split(";")
    # The band is 1080×608; a quarter of the 1312 free rows above it = 328.
    assert steps[0] == "[0:v]crop=1080:608:0:328,scale=1920:1080,setsar=1[base]"


def test_hdr_is_tone_mapped_last_on_the_1080p_picture():
    # As before, tone mapping works on the small final frame, not the raw.
    steps = graph(plan_frame(1080, 1920, framing=Framing.BLURRED), tone_map_hdr=True).split(";")
    assert steps[3] == f"[blurred][fitted]overlay=x=656:y=0:format=auto,{TONE_MAP_HDR_TO_SDR}[base]"
    steps = graph(plan_frame(1080, 1920, framing=Framing.CROP), tone_map_hdr=True).split(";")
    assert steps[0].endswith(f",setsar=1,{TONE_MAP_HDR_TO_SDR}[base]")


# --- Girar ---


@pytest.mark.parametrize(
    ("rotation", "filters"),
    [(90, "transpose=clock"), (180, "hflip,vflip"), (270, "transpose=cclock")],
)
def test_girar_turns_the_picture_first(rotation, filters):
    # A landscape video turned a quarter becomes upright (and gets the
    # blurred sides); a half turn stays 16:9 and keeps the old scaling.
    steps = graph(plan_frame(1920, 1080, rotation=rotation)).split(";")
    if rotation == 180:
        assert steps[0] == f"[0:v]{filters},scale=-2:1080[base]"
    else:
        assert steps[0] == f"[0:v]{filters},split=2[fill][front]"


def test_a_sideways_phone_turned_back_needs_only_the_turn():
    # The file says "upright" (ffmpeg shows 1080×1920) but was filmed
    # sideways: turned 90° it is 16:9, so the old scaling follows the turn.
    steps = graph(plan_frame(1080, 1920, rotation=90)).split(";")
    assert steps[0] == "[0:v]transpose=clock,scale=-2:1080[base]"


# --- Sem áudio ---


def test_sem_audio_leaves_the_sound_out():
    # The worker passes has_audio=False when "Sem áudio" is ticked: no
    # loudnorm step, no audio map, and -an ("no audio") on the output.
    args = build_final_render_command(RAW, OUT, 1.0, 5.0, X264_ENCODER, has_audio=False)
    assert "loudnorm" not in value_after(args, "-filter_complex")
    assert "[audio]" not in args
    assert "-an" in args
    assert "-c:a" not in args


# --- Real ffmpeg ---

ffmpeg_missing = shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None


def stream_facts(path: Path) -> tuple[tuple[int, int], bool]:
    """((width, height) of the video, whether there is an audio stream)."""
    answer = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", str(path)],
        capture_output=True,
        text=True,
        check=True,
    )
    streams = json.loads(answer.stdout)["streams"]
    video = next(s for s in streams if s["codec_type"] == "video")
    return (video["width"], video["height"]), any(s["codec_type"] == "audio" for s in streams)


@pytest.fixture(scope="module")
def upright_phone_clip(tmp_path_factory) -> Path:
    """A 2 s clip with sound, stored 1920×1080 with a "turn me" note, like a phone."""
    folder = tmp_path_factory.mktemp("upright")
    stored = folder / "stored.mp4"
    subprocess.run(
        build_test_clip_command(stored, duration_s=2.0, width=1920, height=1080),
        check=True,
        capture_output=True,
    )
    noted = folder / "phone.mp4"
    # -display_rotation adds the display matrix without touching the frames.
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-display_rotation", "90",
         "-i", str(stored), "-c", "copy", str(noted)],
        check=True,
    )  # fmt: skip
    return noted


@pytest.mark.integration
@pytest.mark.skipif(ffmpeg_missing, reason="ffmpeg/ffprobe not installed")
@pytest.mark.parametrize("framing", list(Framing))
@pytest.mark.parametrize("keep_sound", [True, False], ids=["with-sound", "sem-audio"])
def test_real_ffmpeg_renders_every_framing_at_1920x1080(
    upright_phone_clip, tmp_path, framing, keep_sound
):
    # Setup: the worker's view of the phone clip is 1080×1920 (ffmpeg obeys
    # the note), so the plan fits it into the 16:9 frame with `framing`.
    plan = plan_frame(1080, 1920, framing=framing, crop_position=0.3)
    clip = tmp_path / "clip.mp4"

    # What it does: the real render, without overlay, sound kept or dropped.
    args = build_final_render_command(
        upright_phone_clip, clip, 0.2, 1.2, X264_ENCODER, has_audio=keep_sound, frame_plan=plan
    )
    subprocess.run(args, check=True, capture_output=True)

    # Why this proves it: whatever the framing, the clip is full HD (the
    # old render made 608×1080), and it has sound exactly when asked.
    size, has_audio = stream_facts(clip)
    assert size == (1920, 1080)
    assert has_audio is keep_sound


@pytest.mark.integration
@pytest.mark.skipif(ffmpeg_missing, reason="ffmpeg/ffprobe not installed")
def test_real_ffmpeg_obeys_the_rotation_note_inside_the_filter_graph(upright_phone_clip, tmp_path):
    # The plans above assume ffmpeg turns the stored 1920×1080 frames
    # upright (1080×1920) before our filters see them. The old render
    # (scale=-2:1080) shows it: it makes the narrow 608×1080 clip of the
    # bug report, which only happens if the picture arrived upright.
    clip = tmp_path / "old.mp4"
    args = build_final_render_command(upright_phone_clip, clip, 0.2, 1.2, X264_ENCODER)
    subprocess.run(args, check=True, capture_output=True)
    assert stream_facts(clip)[0] == (608, 1080)
