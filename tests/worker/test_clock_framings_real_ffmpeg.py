"""The clock on a vertical video stays on the 16:9 frame (LIVESTACK-53), with the real ffmpeg.

Background for a beginner: a video filmed upright is fitted into the
1920×1080 clip with a blurred copy behind it or black bars beside it
(design §29). The overlay is drawn on the whole 16:9 frame, so the clock
belongs in the frame's top-right corner, over the blur or the bar, not
squeezed onto the upright picture.

Two kinds of check:
* golden frames (tests/worker/framing_golden.py): the framed picture with
  the overlay, the frozen clock and the result, made by ffmpeg straight to
  PNG (no encoder) and compared with the committed image João checked;
* a real encoded render of an upright clip with black bars, its clock
  read back frame by frame like the 16:9 clips' (test_clock_real_ffmpeg.py).

Skipped when ffmpeg is not installed (CI runs them on the workers' 9.0).
"""

import shutil

import pytest
from PIL import Image

from shared.framing import plan_frame
from shared.models import Framing, RenderClock
from tests.worker.framing_golden import FRAMING_GOLDEN_CASES, SOURCE_SIZE, render_framing_frame
from tests.worker.overlay_golden import CLOCK_TEXT, count_differing_pixels
from tests.worker.test_clock_real_ffmpeg import make_flat_raw, render_with_clock, shown_texts
from worker import overlay_tokens as theme
from worker.overlay import lay_out_overlay

ffmpeg_missing = shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(ffmpeg_missing, reason="ffmpeg/ffprobe not installed"),
]

# The golden frames are scaled and blurred by ffmpeg: allow 1 % of the
# pixels to differ (another ffmpeg version may round a blur differently),
# far below what a moved or missing pill changes.
FRAMING_TOLERANCE_PIXELS = 1920 * 1080 // 100


@pytest.mark.parametrize("case", sorted(FRAMING_GOLDEN_CASES))
def test_a_vertical_video_frame_matches_its_golden_image(tmp_path, case):
    framing, golden_path = FRAMING_GOLDEN_CASES[case]

    frame = render_framing_frame(tmp_path, framing, CLOCK_TEXT)

    golden = Image.open(golden_path)
    assert frame.size == golden.size == (1920, 1080)
    assert count_differing_pixels(frame, golden) <= FRAMING_TOLERANCE_PIXELS


def test_the_clock_sits_beside_the_upright_picture_not_on_it():
    # Where the upright picture goes in the frame, and where the clock goes:
    # the clock's left edge is right of the picture's right edge, i.e.
    # over the blur or the bar, inside the 16:9 frame's safe margins.
    plan = plan_frame(*SOURCE_SIZE, framing=Framing.BLACK_BARS)
    picture_left, _ = plan.fitted_offset
    picture_right = picture_left + plan.fitted_size[0]
    clock = lay_out_overlay(CLOCK_TEXT, frame_width=1920).clock

    assert clock.left > picture_right
    assert clock.left + clock.width == pytest.approx(1920 - theme.CLOCK_RIGHT_PX)


def test_an_upright_clip_with_black_bars_shows_the_clock_frame_by_frame(tmp_path):
    # A real upright clip (360×640), framed with black bars and encoded:
    # the clock, over the bar, starts, runs and freezes exactly as the
    # rule says, like on a 16:9 clip.
    raw = tmp_path / "upright.mp4"
    make_flat_raw(raw, duration_s=3.0, size="360x640")
    clock = RenderClock(largada_s=0.51, official_units=15, decimals=1, unit="s")
    plan = plan_frame(360, 640, framing=Framing.BLACK_BARS)

    render = render_with_clock(
        tmp_path, raw, clock, cut_in_s=0.0, cut_out_s=3.0, frame_plan=plan, background="black"
    )
    rows = shown_texts(render, clock, cut_in_s=0.0)

    assert [shown for _, _, shown in rows] == [expected for _, expected, _ in rows]
    by_time = {time: shown for time, _, shown in rows}
    assert by_time[0.5] is None
    assert by_time[0.5333] == "0,0"
    assert by_time[2.0] == "1,4"
    assert by_time[2.0333] == "1,5"
