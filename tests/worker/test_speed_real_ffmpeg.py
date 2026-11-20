"""The top speed under the clock in a real ffmpeg render, frame by frame (LIVESTACK-67).

What this module is for: "Aceleração e Velocidade" shows the stopwatch
top right and, under it, the run's top speed ("37,2 km/h"). The speed
appears on the frame where the reviewer marked the car crossing the speed
trap and stays to the end of the clip. The worker draws the speed's pill
as one small picture (worker.overlay_speed) and ffmpeg lays it on the
video only from that moment (shared.ffmpeg_clock).

What these tests prove: in a real render, frame by frame, the speed pill
is on exactly the frames shared.pass_clock.speed_shown_at says: not on
the frame just before the mark, on the frame at the mark, and on every
frame after it. Also when the clip starts after the mark.

How a frame is read (for a beginner): the raw video is one flat grey, so
the speed's corner of each frame is either that grey under the still
overlay (no speed) or the speed pill on it. ffmpeg draws both reference
pictures the same way the render does, and each frame is matched to the
closer one.

What they do not cover: how the pill looks (the golden images,
test_overlay_speed.py) or where the speed comes from (the orchestrator's
tests). Skipped when ffmpeg is not installed (CI runs them on the
workers' ffmpeg 9.0).
"""

import shutil
import subprocess
from pathlib import Path

import pytest
from PIL import Image

from shared.ffmpeg import X264_ENCODER, build_final_render_command
from shared.models import RenderClock, RenderSpeed
from shared.pass_clock import speed_shown_at
from tests.worker.test_clock_real_ffmpeg import (
    OVERLAY,
    RAW_COLOUR,
    difference,
    frame_regions,
    frame_times,
    make_flat_raw,
)
from worker.overlay import OverlayText, render_overlay
from worker.overlay_clock import SpeedPill, write_clock_pill
from worker.overlay_speed import write_speed_pill

ffmpeg_missing = shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(ffmpeg_missing, reason="ffmpeg/ffprobe not installed"),
]

# Car 1's first AVF run in the real 2026 snapshots: 4,35 s and 37,18 km/h.
CLOCK = RenderClock(largada_s=0.51, official_units=435, decimals=2, unit="s")


def render_with_speed(
    tmp_path: Path, raw: Path, speed: RenderSpeed, cut_in_s: float, cut_out_s: float
):
    """The worker's render with the overlay, the clock and the speed, as process.py does it.

    Returns the clip, the still overlay, the speed pill's PNG and its region.
    """
    text = OverlayText(**{**OVERLAY.__dict__, "pass_result": None, "clock": CLOCK, "speed": speed})
    overlay_png = tmp_path / "overlay.png"
    layout = render_overlay(overlay_png, text, frame_width=1920)
    assert layout.clock is not None and layout.speed is not None
    speed_png, placed = write_speed_pill(layout.speed, tmp_path, stem="job")
    images = write_clock_pill(
        layout.clock,
        CLOCK,
        cut_in_s,
        tmp_path,
        stem="job",
        speed=SpeedPill(speed_png, placed.position, speed.speed_mark_s),
    )
    clip = tmp_path / "clip.mp4"
    command = build_final_render_command(
        raw, clip, cut_in_s, cut_out_s, X264_ENCODER,
        overlay_image=overlay_png, has_audio=False, clock=images,
    )  # fmt: skip
    finished = subprocess.run(command, capture_output=True, text=True, check=False)
    assert finished.returncode == 0, finished.stderr[-2000:]
    return clip, overlay_png, speed_png, layout.speed.region


def speed_reference(overlay_png: Path, speed_png: Path, region, shown: bool) -> Image.Image:
    """The speed's region on a flat grey frame with the still overlay, with or without the pill."""
    x, y, width, height = region
    steps = "[0:v][1:v]overlay=0:0[still]"
    if shown:
        steps += f";[still][2:v]overlay={x}:{y},crop={width}:{height}:{x}:{y}"
    else:
        steps += f";[still]crop={width}:{height}:{x}:{y}"
    drawn = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-nostdin", "-v", "error",
            "-f", "lavfi", "-i", f"color=c={RAW_COLOUR}:size=1920x1080:rate=1",
            "-i", str(overlay_png), "-i", str(speed_png),
            "-filter_complex", f"{steps},format=yuv420p", "-frames:v", "1",
            "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
        ],
        capture_output=True, check=True,
    )  # fmt: skip
    return Image.frombytes("RGB", (width, height), drawn.stdout)


def speed_rows(tmp_path: Path, raw: Path, speed: RenderSpeed, cut_in_s: float, cut_out_s: float):
    """For every frame: (raw time, whether the rule shows the speed, whether the frame does)."""
    clip, overlay_png, speed_png, region = render_with_speed(
        tmp_path, raw, speed, cut_in_s, cut_out_s
    )
    references = {
        shown: speed_reference(overlay_png, speed_png, region, shown) for shown in (False, True)
    }
    rows = []
    for time_s, picture in zip(frame_times(clip), frame_regions(clip, region), strict=True):
        raw_time = cut_in_s + time_s
        expected = speed_shown_at(raw_time, speed.speed_mark_s)
        shown = min(references, key=lambda candidate: difference(picture, references[candidate]))
        rows.append((round(raw_time, 4), expected, shown))
    return rows


@pytest.fixture(scope="module")
def flat_raw(tmp_path_factory) -> Path:
    raw = tmp_path_factory.mktemp("raw") / "raw.mp4"
    make_flat_raw(raw, duration_s=4.0)
    return raw


def test_the_speed_appears_on_the_marked_frame_and_stays_to_the_end(tmp_path, flat_raw):
    # Arrange: the speed trap marked at 1.51 s raw, between the 30 fps
    # frames that start at 1.5 s and 1.5333 s; the clip runs from 0 to 3 s.
    speed = RenderSpeed(speed_mark_s=1.51, text="37,2", unit="km/h")

    # Act: render it with the real ffmpeg and read every frame back.
    rows = speed_rows(tmp_path, flat_raw, speed, cut_in_s=0.0, cut_out_s=3.0)

    # Assert: every frame agrees with the rule...
    shown_by_frames = [shown for _, _, shown in rows]
    shown_by_rule = [expected for _, expected, _ in rows]
    assert shown_by_frames == shown_by_rule, "each frame must show the speed as the rule says"
    by_time = {time: shown for time, _, shown in rows}
    # ...the frame just before the mark has no speed...
    assert by_time[1.5] is False, "one frame before the speed mark must not show the speed"
    # ...the first frame at or after the mark has it...
    assert by_time[1.5333] is True, "the frame at the speed mark must show the speed"
    # ...and it stays to the last frame of the clip.
    later = {shown for time, _, shown in rows if time >= 1.5333}
    assert later == {True}, "once shown, the speed must stay to the end of the clip"


def test_a_mark_exactly_on_a_frame_shows_the_speed_on_that_frame(tmp_path, flat_raw):
    # Arrange: the mark exactly where the frame at 2.0 s starts (frame 60).
    speed = RenderSpeed(speed_mark_s=2.0, text="34,1", unit="km/h")

    # Act: render and read the frames.
    rows = speed_rows(tmp_path, flat_raw, speed, cut_in_s=0.0, cut_out_s=3.0)

    # Assert: the frame at 1.9667 s has none, the frame at 2.0 s has it.
    by_time = {time: shown for time, _, shown in rows}
    assert by_time[1.9667] is False, "the frame before an on-frame mark must not show the speed"
    assert by_time[2.0] is True, "the frame starting exactly at the mark must show the speed"


def test_a_clip_cut_in_after_the_mark_shows_the_speed_from_its_first_frame(tmp_path, flat_raw):
    # Arrange: the car crossed the trap at 0.8 s; the clip starts at 1.5 s.
    speed = RenderSpeed(speed_mark_s=0.8, text="31,2", unit="km/h")

    # Act: render from 1.5 s to 2.5 s and read the frames.
    rows = speed_rows(tmp_path, flat_raw, speed, cut_in_s=1.5, cut_out_s=2.5)

    # Assert: the speed is on every frame of this clip.
    assert [shown for _, _, shown in rows] == [True] * len(rows), "every frame must show it"
