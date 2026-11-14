"""The page scripts for "Esperar o resultado oficial" (LIVESTACK-62), run in Node.

What this module is for: the box next to "Pronto" is shown only while the
result line or the clock is ticked (static/js/result_wait.js), and the
review request carries `wait_for_result` only with "Pronto" or "Salvar e
reprocessar", and only while the box is shown (static/js/review_form.js).

What these tests prove: the rule that shows the box, that switching a
toggle shows or hides (and enables or disables) it, and exactly when the
request carries the field.

What they do not cover: the real browser (there is no browser test setup);
the pages are in test_result_hold_page.py. The scripts are imported in Node
and given small stand-ins for the form: plain objects with only what the
scripts read.
"""

import json
import shutil
import subprocess

import pytest

from review_ui.catalog import SCRIPT_TEXT
from review_ui.templating import STATIC_DIR

NODE_SCRIPT = """
const input = JSON.parse(process.env.CASES);
globalThis.document = { getElementById: () => ({ textContent: input.catalog }) };
const { collectReviewBody } = await import(input.review_form);
const { resultOrClockIsOn, setupResultWait } = await import(input.result_wait);

// A stand-in for the review form: the fields collectReviewBody reads, plus `extra`.
function form(extra) {
  return { elements: {
    prova: { value: "Manobrabilidade" }, car_number: { value: "26" }, passagem: { value: "2" },
    cut_in_s: { value: "" }, cut_out_s: { value: "" }, show_pass: { checked: true },
    rotation: { value: "0" }, no_audio: { checked: false }, ...extra,
  } };
}
const ticked = { wait_for_result: { checked: true, disabled: false } };
const unticked = { wait_for_result: { checked: false, disabled: false } };
const hidden = { wait_for_result: { checked: true, disabled: true } };

// A stand-in form for setupResultWait: the box, its input, and the listeners.
function pageWithBox(toggles) {
  const waitInput = { disabled: false };
  const box = { hidden: false, querySelector: () => waitInput };
  const listeners = {};
  const page = {
    elements: toggles,
    querySelector: () => box,
    addEventListener: (type, listener) => { listeners[type] = listener; },
  };
  setupResultWait({ form: page });
  const states = [{ hidden: box.hidden, disabled: waitInput.disabled }];
  // The reviewer unticks the result line: a "change" event on the form.
  toggles.show_pass_result.checked = false;
  listeners.change();
  states.push({ hidden: box.hidden, disabled: waitInput.disabled });
  // A prova with a clock is picked: the blocks are swapped in.
  toggles.show_clock = { checked: true };
  listeners["pass-blocks-changed"]();
  states.push({ hidden: box.hidden, disabled: waitInput.disabled });
  return states;
}

console.log(JSON.stringify({
  pronto_ticked: collectReviewBody(form(ticked), 3, "ready"),
  pronto_unticked: collectReviewBody(form(unticked), 3, "ready"),
  pronto_hidden: collectReviewBody(form(hidden), 3, "ready"),
  pronto_without_box: collectReviewBody(form({}), 3, "ready"),
  reprocess_ticked: collectReviewBody(form(ticked), 3, "reprocess"),
  save_ticked: collectReviewBody(form(ticked), 3, "save"),
  on: {
    nothing: resultOrClockIsOn({ elements: {} }),
    result: resultOrClockIsOn({ elements: { show_pass_result: { checked: true } } }),
    clock: resultOrClockIsOn({ elements: { show_clock: { checked: true } } }),
    both_off: resultOrClockIsOn({ elements: {
      show_pass_result: { checked: false }, show_clock: { checked: false } } }),
  },
  box_states: pageWithBox({ show_pass_result: { checked: true } }),
}));
"""


@pytest.fixture(scope="module")
def js() -> dict:
    """What the scripts answered, for every case above (one Node run)."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is not installed; the JavaScript half needs it")
    cases = {
        "catalog": json.dumps(SCRIPT_TEXT),
        "review_form": (STATIC_DIR / "js" / "review_form.js").as_uri(),
        "result_wait": (STATIC_DIR / "js" / "result_wait.js").as_uri(),
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


@pytest.mark.parametrize(
    ("case", "expected"),
    [
        ("pronto_ticked", True),  # the default: Pronto waits for the result
        ("pronto_unticked", False),  # the reviewer unticked it: render at once
        ("reprocess_ticked", True),  # "Salvar e reprocessar" uses the same box
    ],
)
def test_pronto_and_reprocess_send_the_box_as_ticked(js, case, expected):
    # Arrange: the bodies were built in Node by the fixture.

    # Act: look at the body the page would send for this case.
    body = js[case]

    # Assert: the field is there, as ticked.
    assert body["wait_for_result"] is expected, f"{case} must send wait_for_result={expected}"


@pytest.mark.parametrize(
    "case",
    [
        "pronto_hidden",  # the box is hidden (no result or clock on): never sent
        "pronto_without_box",  # no box on the page (an older orchestrator)
        "save_ticked",  # a plain save never starts a wait
    ],
)
def test_the_field_is_left_out_otherwise(js, case):
    # Arrange: the bodies were built in Node by the fixture.

    # Act: look at the body the page would send for this case.
    body = js[case]

    # Assert: no field, so the orchestrator renders at once (or keeps a running wait).
    assert "wait_for_result" not in body, f"{case} must not send wait_for_result"


def test_the_box_shows_while_the_result_or_the_clock_is_on(js):
    # Arrange: the answers of resultOrClockIsOn for four forms, built in Node.

    # Act: read them.
    answers = js["on"]

    # Assert: either toggle ticked shows it; none, or both unticked, hides it.
    assert answers == {"nothing": False, "result": True, "clock": True, "both_off": False}, (
        "the box is shown exactly while the result line or the clock is ticked"
    )


def test_the_box_follows_the_toggles_and_the_swapped_blocks(js):
    # Arrange: the box's state at first, after unticking the result line,
    # and after a prova with a clock was picked (built in Node).

    # Act: read the three states.
    first, result_unticked, clock_swapped_in = js["box_states"]

    # Assert: shown and enabled, then hidden and disabled, then shown again.
    assert first == {"hidden": False, "disabled": False}, "shown while the result is on"
    assert result_unticked == {"hidden": True, "disabled": True}, "hidden and not sent"
    assert clock_swapped_in == {"hidden": False, "disabled": False}, "the clock brings it back"
