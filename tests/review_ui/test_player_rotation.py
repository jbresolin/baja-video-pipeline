"""Turning the video ("Girar") never hides the player's controls (LIVESTACK-51 item 4).

The bug: with "Girar" at 90° or 270°, the job page turned the whole
<video> element, and the browser's own controls (progress bar, play)
turned with it; the page then switched them off, so there was no way to
play or seek with the mouse.

The fix:
* The page has its own control bar (play/pause, one frame back/forward,
  the time, the cut bar as scrubber, the total time) *under* the stage that
  turns, so it is never turned. The browser's controls show only unturned.
* The geometry that turns the picture and places the "Cortar" band is two
  pure functions in static/js/render_options.js (`playerLayout`,
  `cropBandBox`), checked here in Node for 0°, 90°, 180° and 270°.
"""

import json
import re
import shutil
import subprocess

import pytest

from review_ui.catalog import SCRIPT_TEXT
from review_ui.templating import STATIC_DIR
from tests.review_ui.sample_data import make_job

# --- The page: the controls are outside the turned stage ---


def test_the_control_bar_is_outside_the_stage_that_turns(ui, fake_orchestrator):
    # A job turned 90° by the reviewer, with its proxy available.
    fake_orchestrator.add_job(make_job("job-turned", rotation=90))

    html = ui.get("/review/jobs/job-turned").text

    # The stage (what CSS turns) holds only the video and the crop band...
    stage = re.search(r'<div class="player-stage" id="player-stage">(.*?)</div>', html, re.S)
    assert stage is not None
    assert "play-button" not in stage.group(1)
    # ...and the control bar comes after it, with play, frame step, the
    # cut bar and the total time (95,5 s = 1:35,500), labelled in pt-BR.
    after_stage = html[stage.end() :]
    for control in ('id="play-button"', 'data-player="frame-back"', 'data-player="frame-forward"'):
        assert control in after_stage
    assert 'id="cut-bar"' in after_stage
    assert 'id="total-time">1:35,500<' in after_stage
    assert 'aria-label="Controles do player"' in after_stage
    assert 'aria-label="Um quadro para trás (←)"' in after_stage


def test_without_a_proxy_there_are_no_player_buttons(ui, fake_orchestrator):
    # No proxy = no video element: the buttons would do nothing.
    fake_orchestrator.add_job(make_job("job-no-proxy", proxy_available=False, proxy_url=None))

    html = ui.get("/review/jobs/job-no-proxy").text

    assert 'id="play-button"' not in html
    assert 'id="cut-bar"' in html


# --- The geometry, in Node ---

NODE_SCRIPT = """
const input = JSON.parse(process.env.CASES);
globalThis.document = { getElementById: () => ({ textContent: input.catalog }) };
const r = await import(input.module);
console.log(JSON.stringify(input.cases.map(([stage, proxy, rotation, position]) => {
  const layout = r.playerLayout(stage[0], stage[1], proxy[0], proxy[1], rotation);
  const band = layout && r.cropBandBox(layout, proxy[0], proxy[1], rotation, position);
  return { layout, band };
})));
"""

STAGE = (640, 360)  # the player's 16:9 box on the page, in CSS pixels
WIDE_PROXY = (854, 480)  # a 16:9 proxy
UPRIGHT_PROXY = (270, 480)  # a phone filmed upright
ANGLES = [0, 90, 180, 270]

# (stage, proxy, rotation, crop position) for every angle and both shapes,
# plus a stage not laid out yet (no size: nothing to place).
CASES = (
    [(STAGE, WIDE_PROXY, angle, 0.5) for angle in ANGLES]
    + [(STAGE, UPRIGHT_PROXY, angle, 0.0) for angle in ANGLES]
    + [((0, 0), WIDE_PROXY, 90, 0.5)]
)


@pytest.fixture(scope="module")
def results() -> list[dict]:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is not installed; these tests need it")
    payload = {
        "catalog": json.dumps(SCRIPT_TEXT),
        "module": (STATIC_DIR / "js" / "render_options.js").as_uri(),
        "cases": CASES,
    }
    finished = subprocess.run(
        [node, "--input-type=module", "-e", NODE_SCRIPT],
        env={"CASES": json.dumps(payload), "PATH": ""},
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert finished.returncode == 0, finished.stderr
    return json.loads(finished.stdout)


def turned(size, angle):
    """The proxy's shape after a clockwise turn: a quarter turn swaps it."""
    return (size[1], size[0]) if angle % 180 == 90 else size


@pytest.mark.parametrize("index", range(8))
def test_the_turned_picture_fits_the_stage_centred_and_keeps_its_shape(results, index):
    stage, proxy, angle, _ = CASES[index]
    layout = results[index]["layout"]
    # The turned picture is never larger than the stage (nothing is cut
    # off) and it touches two opposite sides (as large as it can be).
    assert layout["pictureWidth"] <= stage[0] + 1e-6
    assert layout["pictureHeight"] <= stage[1] + 1e-6
    assert (
        layout["pictureWidth"] == pytest.approx(stage[0])
        or layout["pictureHeight"] == pytest.approx(stage[1])
    )
    # It has the turned proxy's shape (an upright phone turned 90° is wide).
    turned_width, turned_height = turned(proxy, angle)
    assert layout["pictureWidth"] / layout["pictureHeight"] == pytest.approx(
        turned_width / turned_height
    )
    # It is centred: the same margin on both sides.
    assert layout["left"] == pytest.approx((stage[0] - layout["pictureWidth"]) / 2)
    assert layout["top"] == pytest.approx((stage[1] - layout["pictureHeight"]) / 2)


def test_the_css_turn_for_each_angle(results):
    transforms = [results[index]["layout"]["transform"] for index in range(8)]
    # Unturned: no transform at all. 180°: same size, so no scaling.
    assert transforms[0] == transforms[4] == ""
    assert transforms[2] == transforms[6] == "rotate(180deg) scale(1)"
    # A 16:9 proxy turned a quarter becomes upright: shrunk to the stage's
    # height (360 / 640 = 0.5625).
    assert re.fullmatch(r"rotate\(90deg\) scale\(0\.56\d*\)", transforms[1])
    assert re.fullmatch(r"rotate\(270deg\) scale\(0\.56\d*\)", transforms[3])
    # An upright phone turned a quarter becomes wide: grown to fill the
    # stage (640 / 360 = 1.777…).
    assert re.fullmatch(r"rotate\(90deg\) scale\(1\.77\d*\)", transforms[5])
    assert re.fullmatch(r"rotate\(270deg\) scale\(1\.77\d*\)", transforms[7])


@pytest.mark.parametrize("index", range(8))
def test_the_crop_band_lies_on_the_turned_picture(results, index):
    # The "Cortar" band is drawn over the turned picture, never outside it,
    # and is 16:9 or the whole picture when that is already 16:9.
    layout, band = results[index]["layout"], results[index]["band"]
    assert band["left"] >= layout["left"] - 1e-6
    assert band["top"] >= layout["top"] - 1e-6
    assert band["left"] + band["width"] <= layout["left"] + layout["pictureWidth"] + 1e-6
    assert band["top"] + band["height"] <= layout["top"] + layout["pictureHeight"] + 1e-6
    assert band["width"] / band["height"] == pytest.approx(16 / 9, rel=0.01)


def test_an_upright_phone_unturned_gets_a_full_width_band_at_the_top(results):
    # Position 0 = the top of the picture; the band spans its whole width.
    layout, band = results[4]["layout"], results[4]["band"]
    assert band["top"] == pytest.approx(layout["top"])
    assert band["width"] == pytest.approx(layout["pictureWidth"])


def test_nothing_is_placed_before_the_stage_has_a_size(results):
    assert results[8] == {"layout": None, "band": None}
