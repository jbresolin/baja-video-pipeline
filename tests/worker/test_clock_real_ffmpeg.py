"""The running clock in a real ffmpeg render, checked frame by frame (LIVESTACK-48).

Background for a beginner: the clip's clock is not part of the still
overlay picture. The worker draws its pill (worker.overlay_clock), and
ffmpeg writes the digits on every frame from an expression of the
frame's time (shared.ffmpeg_clock). These tests render real clips with
the real ffmpeg and read the clock back from the frames.

How a frame is read: the raw video is one flat colour, so the clock's
region of a frame is that colour with the clock on top. For the
candidates (no clock, and the clock at a few counts around the expected
one) ffmpeg draws a reference picture the same way, with the count
written as plain text, and we keep the candidate that looks most like
the frame. The test passes when the closest candidate is exactly the
value shared.pass_clock says, at every frame: at the largada, mid-run,
one frame before the freeze, at the freeze and after it.

Skipped when ffmpeg is not installed. Run only these with
    uv run pytest tests/worker/test_clock_real_ffmpeg.py
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from PIL import Image, ImageChops

from shared.ffmpeg import X264_ENCODER, build_final_render_command
from shared.ffmpeg_clock import ClockOverlay, drawtext_filter
from shared.models import RenderClock
from shared.pass_clock import clock_text, clock_units, elapsed_microseconds
from worker.overlay import OverlayText, render_overlay
from worker.overlay_clock import ClockLayout, write_clock_pill

ffmpeg_missing = shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(ffmpeg_missing, reason="ffmpeg/ffprobe not installed"),
]

FRAME_RATE = 30
# A dark grey, like asphalt: the clock's slate pill and white digits show on it.
RAW_COLOUR = "0x505050"
OVERLAY = OverlayText(
    prova="Manobrabilidade",
    car_number=26,
    team_name="Saci Baja",
    passagem=2,
    show_pass=True,
    school_short="UNIFEI",
    pass_result="66 s · 4 cones → 86 s",
)


def make_flat_raw(path: Path, duration_s: float) -> None:
    """A silent 30 fps clip of one colour: frame k starts at exactly k/30 s."""
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-nostdin", "-y",
            "-f", "lavfi", "-i", f"color=c={RAW_COLOUR}:size=640x360:rate={FRAME_RATE}",
            "-t", f"{duration_s}", "-c:v", X264_ENCODER, "-preset", "ultrafast",
            "-pix_fmt", "yuv420p", str(path),
        ],
        check=True,
        capture_output=True,
    )  # fmt: skip


class Render:
    """A rendered clip and what it was drawn with."""

    def __init__(self, clip: Path, layout: ClockLayout, overlay_png: Path, clock: ClockOverlay):
        self.clip, self.layout, self.overlay_png, self.clock = clip, layout, overlay_png, clock
        # Reference pictures already drawn, by text (None = no clock).
        self.references: dict[str | None, Image.Image] = {}


def render_with_clock(
    tmp_path: Path, raw: Path, clock: RenderClock, cut_in_s: float, cut_out_s: float
) -> Render:
    """The worker's render of `raw` with the overlay and the clock, as process.py does it."""
    text = OverlayText(**{**OVERLAY.__dict__, "clock": clock})
    overlay_png = tmp_path / "overlay.png"
    layout = render_overlay(overlay_png, text, frame_width=1920)
    assert layout is not None
    images = write_clock_pill(layout, clock, cut_in_s, tmp_path, stem="job")
    clip = tmp_path / "clip.mp4"
    command = build_final_render_command(
        raw,
        clip,
        cut_in_s,
        cut_out_s,
        X264_ENCODER,
        overlay_image=overlay_png,
        has_audio=False,
        clock=images,
    )
    finished = subprocess.run(command, capture_output=True, text=True, check=False)
    assert finished.returncode == 0, finished.stderr[-2000:]
    return Render(clip, layout, overlay_png, images)


def frame_times(clip: Path) -> list[float]:
    """Each output frame's time in the clip (its `t` in the filters), in order."""
    probe = subprocess.run(
        [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "frame=pts_time", "-of", "json", str(clip),
        ],
        capture_output=True, text=True, check=True,
    )  # fmt: skip
    return [float(frame["pts_time"]) for frame in json.loads(probe.stdout)["frames"]]


def clock_regions(clip: Path, layout: ClockLayout) -> list[Image.Image]:
    """The clock's region of every frame, as RGB pictures."""
    x, y, width, height = layout.region
    decoded = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-i", str(clip),
            "-vf", f"crop={width}:{height}:{x}:{y}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
        ],
        capture_output=True, check=True,
    )  # fmt: skip
    size = width * height * 3
    data = decoded.stdout
    return [
        Image.frombytes("RGB", (width, height), data[start : start + size])
        for start in range(0, len(data), size)
    ]


def difference(first: Image.Image, second: Image.Image) -> int:
    """How unlike two pictures are: the sum of every channel's difference."""
    histogram = ImageChops.difference(first, second).histogram()
    return sum(value * count for value, count in zip(list(range(256)) * 3, histogram, strict=True))


def reference(render: Render, text: str | None) -> Image.Image:
    """The clock's region on a flat frame with `text` written by ffmpeg the render's way."""
    if text not in render.references:
        x, y, width, height = render.layout.region
        steps = "[0:v][1:v]overlay=0:0[still]"
        if text is None:
            steps += f";[still]crop={width}:{height}:{x}:{y}"
        else:
            pill_x, pill_y = render.clock.pill_position
            steps += (
                f";[still][2:v]overlay={pill_x}:{pill_y},"
                f"{drawtext_filter(render.clock, text, None)},crop={width}:{height}:{x}:{y}"
            )
        drawn = subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-nostdin", "-v", "error",
                "-f", "lavfi", "-i", f"color=c={RAW_COLOUR}:size=1920x1080:rate=1",
                "-i", str(render.overlay_png), "-i", str(render.clock.pill_png),
                "-filter_complex", f"{steps},format=yuv420p", "-frames:v", "1",
                "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
            ],
            capture_output=True, check=True,
        )  # fmt: skip
        render.references[text] = Image.frombytes("RGB", (width, height), drawn.stdout)
    return render.references[text]


def read_clock(region: Image.Image, render: Render, candidates: set[str | None]) -> str | None:
    """Which candidate text (None = no clock) the frame's region shows."""
    scores = {text: difference(region, reference(render, text)) for text in candidates}
    return min(scores, key=scores.get)


def as_text(units: int | None, decimals: int) -> str | None:
    return None if units is None else clock_text(units, decimals)


def shown_texts(render: Render, clock: RenderClock, cut_in_s: float):
    """For every frame: (raw time, what the rule says, what the frame shows) as texts."""
    rows = []
    frames = zip(frame_times(render.clip), clock_regions(render.clip, render.layout), strict=True)
    for time_s, region in frames:
        raw_time = cut_in_s + time_s
        expected = clock_units(
            elapsed_microseconds(raw_time, clock.largada_s), clock.official_units, clock.decimals
        )
        # The expected count, its neighbours, and no clock at all: a frame
        # one step early or late, or a clock shown too soon, would win.
        counts = {0, 1} if expected is None else {max(0, expected - 1), expected, expected + 1}
        candidates = {None} | {clock_text(count, clock.decimals) for count in counts}
        shown = read_clock(region, render, candidates)
        rows.append((round(raw_time, 4), as_text(expected, clock.decimals), shown))
    return rows


@pytest.fixture(scope="module")
def flat_raw(tmp_path_factory) -> Path:
    raw = tmp_path_factory.mktemp("raw") / "raw.mp4"
    make_flat_raw(raw, duration_s=13.0)
    return raw


def test_the_clock_starts_runs_and_freezes_frame_by_frame(tmp_path, flat_raw):
    # The largada falls between two frames (0.51 s: frames start at 0.5 and
    # 0.5333…), and the official time is 1.5 s with one decimal (15 units),
    # so the run ends at raw 2.01 s, also between frames.
    clock = RenderClock(largada_s=0.51, official_units=15, decimals=1)
    render = render_with_clock(tmp_path, flat_raw, clock, cut_in_s=0.0, cut_out_s=3.0)

    rows = shown_texts(render, clock, cut_in_s=0.0)

    # Every frame shows exactly what the rule says.
    assert [shown for _, _, shown in rows] == [expected for _, expected, _ in rows]
    by_time = {time: shown for time, _, shown in rows}
    # Before the largada: no clock (the frame at 0.5 s is before 0.51 s).
    assert by_time[0.0] is None
    assert by_time[0.5] is None
    # The largada frame (the first at or after 0.51 s) shows 0,0.
    assert by_time[0.5333] == "0,0"
    # Mid-run: 1.0 s is 0.49 s after the largada → 0,4 (rounded down).
    assert by_time[1.0] == "0,4"
    # One frame before the freeze (2.0 s: 1.49 s run) and at it (1.5233 s ≥ 1.5).
    assert by_time[2.0] == "1,4"
    assert by_time[2.0333] == "1,5"
    # After the freeze it stays, to the end of the clip.
    assert {shown for time, _, shown in rows if time >= 2.0333} == {"1,5"}


def test_a_cut_in_after_the_largada_starts_with_the_clock_running(tmp_path, flat_raw):
    # Largada at 1.0 s, official 10,3 s; the clip starts at 9.5 s raw, so
    # its first frame already shows 8,5, and the tens place appears at 11.0 s.
    clock = RenderClock(largada_s=1.0, official_units=103, decimals=1)
    render = render_with_clock(tmp_path, flat_raw, clock, cut_in_s=9.5, cut_out_s=12.0)

    rows = shown_texts(render, clock, cut_in_s=9.5)

    assert [shown for _, _, shown in rows] == [expected for _, expected, _ in rows]
    assert rows[0][2] == "8,5"
    by_time = {time: shown for time, _, shown in rows}
    # "9,9" with the tens place blank, then "10,0" (right-aligned).
    assert by_time[10.9667] == "9,9"
    assert by_time[11.0] == "10,0"
    # Frozen on 10,3 from raw 11.3 s on.
    assert by_time[11.2667] == "10,2"
    assert by_time[11.3] == "10,3"
    assert rows[-1][2] == "10,3"


def test_a_cut_in_before_the_largada_shows_nothing_until_it(tmp_path, flat_raw):
    clock = RenderClock(largada_s=2.0, official_units=3, decimals=0)
    render = render_with_clock(tmp_path, flat_raw, clock, cut_in_s=1.0, cut_out_s=6.0)

    rows = shown_texts(render, clock, cut_in_s=1.0)

    assert [shown for _, _, shown in rows] == [expected for _, expected, _ in rows]
    by_time = {time: shown for time, _, shown in rows}
    assert by_time[1.9667] is None
    # Whole seconds: 0 on the largada frame, 1 a second later, frozen on 3.
    assert by_time[2.0] == "0"
    assert by_time[2.9667] == "0"
    assert by_time[3.0] == "1"
    assert by_time[5.0] == "3"
    assert by_time[5.9667] == "3"
