"""The page scripts for the speed mark (LIVESTACK-67), run in Node.

What this module is for: for "Aceleração e Velocidade" the job page's
"Cronômetro" block has a second mark, "Velocidade": where the car crosses
the speed trap, so the top speed appears under the clock from there. The
scripts:

* js/player.js: "Marcar" or the v key writes the current frame's time,
  rounded down to the millisecond (like the largada, so the marked frame
  shows the speed), -/= nudge it by a frame, a second line on the cut bar,
  "Limpar" empties it;
* js/review_form.js: sends speed_mark_s, or clear_speed_mark when empty;
* js/speed_order.js: warns while the speed mark is before the largada.

What these tests prove: those behaviours, on small stand-ins for the page
(plain objects with only what the scripts read and write).

What they do not cover: a real browser (there is no browser test setup);
the pages themselves are in test_avf_speed_page.py.
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

const { collectReviewBody } = await import(input.review_form);
const { setupPlayer } = await import(input.player);
const { speedMarkBeforeLargada, setupSpeedOrder } = await import(input.speed_order);

// A form field the scripts can read and write. Its events reach the form's
// listeners, as a real field's "input" bubbles up to its form.
function field(value) {
  return {
    value,
    dispatchEvent(event) { this.owner?.fire(event.type, { target: this }); },
  };
}

// The review form with the clock block of AVF: largada and speed fields.
function reviewForm(fields) {
  const listeners = {};
  for (const [name, input] of Object.entries(fields)) {
    if (name.startsWith("__")) continue;
    input.name = name;
    input.owner = { fire: (type, event) => form.fire(type, event) };
  }
  const form = {
    elements: fields,
    listeners,
    querySelectorAll: () => [],
    querySelector: (selector) => (selector === "#pass-speed-early" ? fields.__warning : null),
    addEventListener: (type, listener) => { (listeners[type] ??= []).push(listener); },
    fire(type, event = {}) { for (const listener of listeners[type] ?? []) listener(event); },
  };
  return form;
}

// --- The player: the v key, the nudge, the cut-bar line, "Limpar". ---
const fields = {
  cut_in_s: field(""), cut_out_s: field(""), largada_s: field("1.000"), speed_mark_s: field(""),
};
const form = reviewForm(fields);
// The frame at 4.0333… s of a 30 fps proxy, in a 10 s video.
const video = {
  currentTime: 4.033333333333333, duration: 10, paused: true,
  addEventListener() {}, pause() {}, play() {},
};
const jobElement = { dataset: { frameRate: "30", duration: "10" } };
const player = setupPlayer({ video, form, jobElement });
const vHandled = player.handleKey({ key: "v", shiftKey: false });
const afterV = fields.speed_mark_s.value;
const speedLine = elements["speed-mark"];
const lineAfterV = { hidden: speedLine.hidden, left: speedLine.style.left };
player.handleKey({ key: "=", shiftKey: false });
const afterNudge = fields.speed_mark_s.value;
form.fire("click", { target: { closest: (selector) => selector === "[data-clear-speed-mark]" } });
const afterClear = fields.speed_mark_s.value;
const lineAfterClear = elements["speed-mark"].hidden;

// Without the speed field (a prova without a speed), the v key does nothing.
const plainForm = reviewForm({ cut_in_s: field(""), cut_out_s: field("") });
const plainPlayer = setupPlayer({ video, form: plainForm, jobElement });
const vWithoutField = plainPlayer.handleKey({ key: "v", shiftKey: false });

// --- The request body. ---
function body(extra) {
  return collectReviewBody({ elements: {
    prova: { value: "Aceleração e Velocidade" },
    car_number: { value: "1" }, passagem: { value: "1" },
    cut_in_s: { value: "" }, cut_out_s: { value: "" }, show_pass: { checked: true },
    rotation: { value: "0" }, no_audio: { checked: false }, ...extra,
  } }, 3, "save");
}

// --- The warning: shown and hidden as the marks change. ---
const warning = { hidden: true };
const orderFields = { largada_s: field("3.000"), speed_mark_s: field("4.000"), __warning: warning };
const orderForm = reviewForm(orderFields);
setupSpeedOrder({ form: orderForm });
const warningStates = [warning.hidden];
orderFields.speed_mark_s.value = "2.500";
orderForm.fire("input");
warningStates.push(warning.hidden);
orderFields.largada_s.value = "";
orderForm.fire("input");
warningStates.push(warning.hidden);

console.log(JSON.stringify({
  vHandled, afterV, lineAfterV, afterNudge, afterClear, lineAfterClear, vWithoutField,
  marked: body({ speed_mark_s: { value: "4.033" } }),
  cleared: body({ speed_mark_s: { value: "" } }),
  withoutField: body({}),
  order: {
    after: speedMarkBeforeLargada({ largada: 1.0, speedMark: 4.0 }),
    before: speedMarkBeforeLargada({ largada: 3.0, speedMark: 2.5 }),
    same: speedMarkBeforeLargada({ largada: 1.0, speedMark: 1.0 }),
    floatTrap: speedMarkBeforeLargada({ largada: 0.3, speedMark: 0.1 + 0.2 }),
    noLargada: speedMarkBeforeLargada({ largada: null, speedMark: 2.0 }),
    noSpeed: speedMarkBeforeLargada({ largada: 2.0, speedMark: null }),
  },
  warningStates,
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
        "review_form": (STATIC_DIR / "js" / "review_form.js").as_uri(),
        "player": (STATIC_DIR / "js" / "player.js").as_uri(),
        "speed_order": (STATIC_DIR / "js" / "speed_order.js").as_uri(),
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


def test_the_v_key_marks_the_current_frame_rounded_down(js):
    # Arrange: the player on the frame at 4.0333… s (done in the script).

    # Act: the script pressed v.
    written = js["afterV"]

    # Assert: the key was taken, and the mark is the frame's time rounded
    # down to the millisecond, so that frame is at or after the mark.
    assert js["vHandled"] is True, "v must be a player shortcut while the speed field is there"
    assert written == "4.033", "the speed mark must be rounded down like the largada"


def test_the_mark_gets_its_own_line_on_the_cut_bar(js):
    # Arrange and act: the script marked the speed at 4.033 s of a 10 s video.
    line = js["lineAfterV"]

    # Assert: the line is shown at 40.33 % of the bar.
    assert line["hidden"] is False, "the speed mark's line must be shown"
    assert line["left"].startswith("40.33"), "at the mark's place on the cut bar"


def test_the_nudge_moves_the_speed_mark_by_one_frame(js):
    # Arrange: the speed mark was the last one set, so it is the active one.

    # Act: the script pressed = once.
    nudged = js["afterNudge"]

    # Assert: one 30 fps frame later, still rounded down.
    assert nudged == "4.066", "= must move the active speed mark one frame later"


def test_limpar_empties_the_speed_mark_and_hides_its_line(js):
    # Arrange and act: the script clicked "Limpar" next to the speed mark.

    # Assert: the field is empty and the line gone.
    assert js["afterClear"] == "", "Limpar must empty the speed mark"
    assert js["lineAfterClear"] is True, "and hide its line on the cut bar"


def test_without_the_speed_field_the_v_key_does_nothing(js):
    # Arrange and act: a page without a speed (any prova but AVF), v pressed.

    # Assert: not a shortcut there.
    assert js["vWithoutField"] is False, "v must do nothing without a speed field"


def test_the_review_request_carries_the_speed_mark(js):
    # Arrange and act: the script built the request bodies.
    marked, cleared, without = js["marked"], js["cleared"], js["withoutField"]

    # Assert: a mark is sent as seconds; an empty field asks to clear it;
    # without the field (no speed) neither is sent, so the saved one stays.
    assert marked["speed_mark_s"] == 4.033, "the mark must be sent in seconds"
    assert "clear_speed_mark" not in marked, "a set mark must not be cleared"
    assert cleared["clear_speed_mark"] is True, "an empty field must remove the mark"
    assert "speed_mark_s" not in cleared, "and send no mark"
    assert "speed_mark_s" not in without, "no field, nothing about the speed"
    assert "clear_speed_mark" not in without, "and nothing cleared"


# Each row: which case of speedMarkBeforeLargada, the answer, and why it exists.
ORDER_CASES = [
    # The usual order: the trap is after the start.
    pytest.param("after", False, id="speed-after-largada"),
    # The slip the warning is for.
    pytest.param("before", True, id="speed-before-largada"),
    # Both on the same frame: not "before".
    pytest.param("same", False, id="same-moment"),
    # 0.1 + 0.2 is 0.30000000000000004: still the same microsecond as 0.3.
    pytest.param("floatTrap", False, id="float-trap"),
    # Only one mark set: nothing to compare, no warning.
    pytest.param("noLargada", False, id="no-largada"),
    pytest.param("noSpeed", False, id="no-speed-mark"),
]


@pytest.mark.parametrize(("case", "expected"), ORDER_CASES)
def test_the_warning_rule_compares_the_marks_in_whole_microseconds(js, case, expected):
    # Arrange and act: the script asked the rule for each case.
    answer = js["order"][case]

    # Assert: the expected answer.
    assert answer is expected, f"speedMarkBeforeLargada must say {expected} for {case}"


def test_the_warning_follows_the_marks_as_they_change(js):
    # Arrange: largada 3 s, speed 4 s (fine); then speed 2.5 s (before);
    # then the largada emptied (nothing to compare).

    # Act: the script recorded `hidden` after each step.
    states = js["warningStates"]

    # Assert: hidden, shown, hidden.
    assert states == [True, False, True], "the warning must follow every change of the marks"
