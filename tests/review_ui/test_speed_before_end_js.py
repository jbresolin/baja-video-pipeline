"""The page scripts when the speed is shown N seconds before the clip's end (LIVESTACK-68), in Node.

What this module is for: for a prova whose speed block has
`seconds_before_end`, the job page's "Cronômetro" block has no
"Velocidade" mark; it carries N in data-speed-seconds-before-end instead.
js/player.js then:

* draws the green line on the cut bar at the computed start (the form's
  cut-out minus N, or the video's end minus N without a cut-out, never
  before the cut-in), and moves it whenever the cut-out moves;
* ignores the v key (there is no mark to set);
* answers player.speedStart(), which js/overlay_preview.js uses to draw the
  speed from the same moment, before a save. In the mark mode it is the
  speed mark field, as before.

What these tests prove: those behaviours, on small stand-ins for the page
(plain objects with only what the scripts read and write). The rule itself
is checked against the shared vectors in
tests/shared/test_speed_before_end_rule.py.

What they do not cover: a real browser (there is no browser test setup);
the mark mode's own behaviour (test_avf_speed_js.py, unchanged).
"""

import json
import shutil
import subprocess

import pytest

from review_ui.catalog import SCRIPT_TEXT
from review_ui.templating import STATIC_DIR

NODE_SCRIPT = """
const input = JSON.parse(process.env.CASES);

// --- Stand-ins for the page: every element the player looks up. ---
const elements = {};
function fakeElement() {
  return {
    hidden: true, textContent: input.catalog, style: {}, dataset: {}, title: "",
    classList: { toggle() {} }, addEventListener() {}, setAttribute() {},
    getBoundingClientRect: () => ({ left: 0, width: 100 }),
    setPointerCapture() {}, hasPointerCapture: () => false,
  };
}
globalThis.document = {
  getElementById: (id) => (elements[id] ??= fakeElement()),
  querySelector: () => fakeElement(),
};

const { setupPlayer } = await import(input.player);

// A form field the scripts can read and write; its events reach the form.
function field(value) {
  return {
    value,
    dispatchEvent(event) { this.owner?.fire(event.type, { target: this }); },
  };
}

// The review form. `clockBlock` is the "Cronômetro" fieldset, found by
// the attribute that carries N; null = no such block (the mark mode).
function reviewForm(fields, clockBlock) {
  const listeners = {};
  for (const [name, input] of Object.entries(fields)) {
    input.name = name;
    input.owner = { fire: (type, event) => form.fire(type, event) };
  }
  const form = {
    elements: fields,
    querySelectorAll: () => [],
    querySelector: (selector) =>
      selector === "[data-speed-seconds-before-end]" ? clockBlock : null,
    addEventListener: (type, listener) => { (listeners[type] ??= []).push(listener); },
    fire(type, event = {}) { for (const listener of listeners[type] ?? []) listener(event); },
  };
  return form;
}

// A 10 s video, paused on the frame at 4.0333… s of a 30 fps proxy.
const video = {
  currentTime: 4.033333333333333, duration: 10, paused: true,
  addEventListener() {}, pause() {}, play() {},
};
const jobElement = { dataset: { frameRate: "30", duration: "10" } };

// --- End mode: N = 3, cut 0.5–5.0 s, no speed mark field. ---
const fields = { cut_in_s: field("0.500"), cut_out_s: field("5.000"), largada_s: field("1.000") };
const block = { dataset: { speedSecondsBeforeEnd: "3.0" } };
const form = reviewForm(fields, block);
const player = setupPlayer({ video, form, jobElement });
// The green line on the cut bar: whether it is shown, and where.
const line = () => {
  const mark = elements["speed-mark"];
  return { hidden: mark.hidden, left: mark.style.left };
};
const atStart = { start: player.speedStart(), line: line() };

// The reviewer presses v: nothing to mark.
const vHandled = player.handleKey({ key: "v", shiftKey: false });

// The reviewer moves the cut-out to 6.25 s (typing, or "o"; both send "input").
fields.cut_out_s.value = "6.250";
fields.cut_out_s.dispatchEvent({ type: "input" });
const afterCutOutMoved = { start: player.speedStart(), line: line() };

// Then empties the cut-out: the clip ends with the video (10 s).
fields.cut_out_s.value = "";
fields.cut_out_s.dispatchEvent({ type: "input" });
const withoutCutOut = { start: player.speedStart(), line: line() };

// A clip shorter than N: 3.5–5.0 s.
fields.cut_in_s.value = "3.500";
fields.cut_out_s.value = "5.000";
fields.cut_out_s.dispatchEvent({ type: "input" });
const shortClip = { start: player.speedStart(), line: line() };

// No cut-out and no duration known (data-duration empty): no start, no line.
const unknownForm = reviewForm({ cut_in_s: field(""), cut_out_s: field("") }, block);
const unknownPlayer = setupPlayer({
  video: null, form: unknownForm, jobElement: { dataset: { frameRate: "30", duration: "" } },
});
const unknownEnd = { start: unknownPlayer.speedStart(), line: line() };

// --- Mark mode: no N, the speed mark field at 4.033 s. ---
const markForm = reviewForm(
  { cut_in_s: field("0.500"), cut_out_s: field("5.000"), speed_mark_s: field("4.033") }, null,
);
const markPlayer = setupPlayer({ video, form: markForm, jobElement });
const markMode = { start: markPlayer.speedStart(), line: line() };

console.log(JSON.stringify({
  atStart, vHandled, afterCutOutMoved, withoutCutOut, shortClip, unknownEnd, markMode,
}));
"""


@pytest.fixture(scope="module")
def js() -> dict:
    """Everything the Node script printed, as one dict."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is not installed; the JavaScript half needs it")
    cases = {
        "catalog": json.dumps(SCRIPT_TEXT),
        "player": (STATIC_DIR / "js" / "player.js").as_uri(),
    }
    finished = subprocess.run(
        [node, "--input-type=module", "-e", NODE_SCRIPT],
        env={"CASES": json.dumps(cases), "PATH": ""},
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert finished.returncode == 0, finished.stderr
    return json.loads(finished.stdout)


def test_the_green_line_is_at_the_computed_start(js):
    # Arrange: N = 3 s, the clip cut 0.5–5.0 s in a 10 s video (done in the script).

    # Act: read where the player put the start and the line when it set up.
    state = js["atStart"]

    # Assert: 5.0 − 3 = 2.0 s, which is 20 % along the cut bar.
    assert state["start"] == 2.0, "the speed starts 3 s before the cut-out"
    assert state["line"]["hidden"] is False, "the green line must be shown"
    assert state["line"]["left"] == "20%", "at 2.0 s of a 10 s video"


def test_the_v_key_does_nothing(js):
    # Arrange: the end mode's block, which has no speed mark field.

    # Act: the script pressed v.
    handled = js["vHandled"]

    # Assert: not a shortcut here, so the page lets the key go.
    assert handled is False, "v must be ignored when there is no mark to set"


def test_the_line_follows_the_cut_out(js):
    # Arrange: the cut-out moved from 5.0 s to 6.25 s (done in the script).

    # Act: read the start and the line after the move.
    state = js["afterCutOutMoved"]

    # Assert: 6.25 − 3 = 3.25 s, at 32.5 % of the bar.
    assert state["start"] == 3.25, "the start must follow the cut-out"
    assert state["line"]["left"] == "32.5%", "and so must the line"


def test_without_a_cut_out_the_end_of_the_video_is_used(js):
    # Arrange: the cut-out field emptied (done in the script).

    # Act: read the start and the line.
    state = js["withoutCutOut"]

    # Assert: the 10 s video's end minus 3 s, as the orchestrator does.
    assert state["start"] == 7.0, "the video's end minus 3 s"
    assert state["line"]["left"] == "70%", "the line at 7 s"


def test_a_clip_shorter_than_n_starts_at_the_cut_in(js):
    # Arrange: the clip cut 3.5–5.0 s, shorter than 3 s (done in the script).

    # Act: read the start.
    state = js["shortClip"]

    # Assert: never before the cut-in: the whole clip shows the speed.
    assert state["start"] == 3.5, "the start is the cut-in"
    assert state["line"]["left"] == "35%", "the line at the cut-in"


def test_with_no_end_known_there_is_no_start(js):
    # Arrange: no cut-out, and the page knows no duration (done in the script).

    # Act: read the start.
    state = js["unknownEnd"]

    # Assert: nothing to draw from.
    assert state["start"] is None, "no end of the clip, no start"


def test_in_mark_mode_the_start_is_the_speed_mark(js):
    # Arrange: no N on the page, the speed mark at 4.033 s (done in the script).

    # Act: read the start the preview would use and the line.
    state = js["markMode"]

    # Assert: the mark, exactly as before LIVESTACK-68.
    assert state["start"] == 4.033, "the preview draws the speed from the mark"
    assert state["line"]["left"].startswith("40.33"), "the line at the mark"
