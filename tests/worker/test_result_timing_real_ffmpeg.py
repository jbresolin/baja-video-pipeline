"""With a clock, the result appears when the clock stops: a real ffmpeg render (LIVESTACK-53).

Background for a beginner: a clip with a running clock used to show the
result pill (time, penalties, corrected time) for the whole clip, which
gave the time away before the car had finished. Now the result pill is a
picture of its own, which ffmpeg lays on the video only from the frame
whose clock shows the official time, to the end of the clip.

How a frame is read: the raw video is one flat colour, so the result's
region of each frame is either that colour under the still overlay (no
result) or the result pill. ffmpeg draws both reference pictures the same
way the render does, and each frame is matched to the closer one. The
test passes when that is exactly what shared.pass_clock.result_shown_at
says, at every frame, in particular one frame before the freeze and at it.

Skipped when ffmpeg is not installed (CI runs it on the workers' 9.0).
"""

import shutil
import subprocess
from pathlib import Path

import pytest
from PIL import Image

from shared.models import RenderClock
from shared.pass_clock import result_shown_at
from tests.worker.test_clock_real_ffmpeg import (
    RAW_COLOUR,
    Render,
    difference,
    frame_regions,
    frame_times,
    make_flat_raw,
    render_with_clock,
)

ffmpeg_missing = shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(ffmpeg_missing, reason="ffmpeg/ffprobe not installed"),
]


def result_region(render: Render) -> tuple[int, int, int, int]:
    """Where the result pill's own picture goes in the frame (even edges)."""
    x, y = render.clock.result_position
    width, height = Image.open(render.clock.result_png).size
    return x, y, width, height


def result_reference(render: Render, shown: bool) -> Image.Image:
    """The result's region on a flat frame with the still overlay, with or without the pill."""
    x, y, width, height = result_region(render)
    steps = "[0:v][1:v]overlay=0:0[still]"
    if shown:
        steps += f";[still][2:v]overlay={x}:{y},crop={width}:{height}:{x}:{y}"
    else:
        steps += f";[still]crop={width}:{height}:{x}:{y}"
    drawn = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-nostdin", "-v", "error",
            "-f", "lavfi", "-i", f"color=c={RAW_COLOUR}:size=1920x1080:rate=1",
            "-i", str(render.overlay_png), "-i", str(render.clock.result_png),
            "-filter_complex", f"{steps},format=yuv420p", "-frames:v", "1",
            "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
        ],
        capture_output=True, check=True,
    )  # fmt: skip
    return Image.frombytes("RGB", (width, height), drawn.stdout)


def result_rows(render: Render, clock: RenderClock, cut_in_s: float):
    """For every frame: (raw time, whether the rule shows the result, whether the frame does)."""
    references = {shown: result_reference(render, shown) for shown in (False, True)}
    rows = []
    frames = zip(
        frame_times(render.clip), frame_regions(render.clip, result_region(render)), strict=True
    )
    for time_s, region in frames:
        raw_time = cut_in_s + time_s
        expected = result_shown_at(raw_time, clock.largada_s, clock.official_units, clock.decimals)
        shown = min(references, key=lambda candidate: difference(region, references[candidate]))
        rows.append((round(raw_time, 4), expected, shown))
    return rows


@pytest.fixture(scope="module")
def flat_raw(tmp_path_factory) -> Path:
    raw = tmp_path_factory.mktemp("raw") / "raw.mp4"
    make_flat_raw(raw, duration_s=4.0)
    return raw


def test_the_result_appears_on_the_freeze_frame_and_stays(tmp_path, flat_raw):
    # Largada at 0.51 s, official time 1,5 s: the clock freezes at raw
    # 2.01 s, between the frames at 2.0 s and 2.0333 s (30 fps).
    clock = RenderClock(largada_s=0.51, official_units=15, decimals=1)
    render = render_with_clock(tmp_path, flat_raw, clock, cut_in_s=0.0, cut_out_s=3.0)
    assert render.clock.result_png is not None

    rows = result_rows(render, clock, cut_in_s=0.0)

    # Every frame agrees with the rule.
    assert [shown for _, _, shown in rows] == [expected for _, expected, _ in rows]
    by_time = {time: shown for time, _, shown in rows}
    # Before the largada and while the clock runs: no result.
    assert by_time[0.0] is False
    assert by_time[1.0] is False
    # One frame before the freeze (the clock shows 1,4): still no result.
    assert by_time[2.0] is False
    # The freeze frame (the clock shows 1,5): the result is there...
    assert by_time[2.0333] is True
    # ...and stays to the end of the clip.
    assert {shown for time, _, shown in rows if time >= 2.0333} == {True}


def test_a_clip_cut_in_after_the_freeze_shows_the_result_from_its_first_frame(tmp_path, flat_raw):
    # The car finished before the clip starts: frozen clock and result
    # from the first frame, as a clip without a running part.
    clock = RenderClock(largada_s=0.2, official_units=10, decimals=1)
    render = render_with_clock(tmp_path, flat_raw, clock, cut_in_s=1.5, cut_out_s=2.5)

    rows = result_rows(render, clock, cut_in_s=1.5)

    assert [shown for _, _, shown in rows] == [True] * len(rows)
