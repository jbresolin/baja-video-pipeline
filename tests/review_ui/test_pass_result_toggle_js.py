"""static/js/review_form.js and "Mostrar resultado da passagem" (LIVESTACK-47), run in Node.

The box is only on the job page for a prova with results configured. The
script must then send `show_pass_result` as ticked, and leave it out
otherwise: left out means "keep" for the orchestrator, so saving a job of
another prova never changes the choice. The tests import the script's
collectReviewBody() in Node and give it a stand-in for the form.
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
const { collectReviewBody } = await import(input.module);
// A stand-in for the <form>: only what collectReviewBody reads.
function form(extra) {
  return { elements: {
    prova: { value: "Manobrabilidade" }, car_number: { value: "26" }, passagem: { value: "2" },
    cut_in_s: { value: "" }, cut_out_s: { value: "" }, show_pass: { checked: true },
    rotation: { value: "0" }, no_audio: { checked: false }, ...extra,
  } };
}
console.log(JSON.stringify({
  without: collectReviewBody(form({}), 3, "save"),
  ticked: collectReviewBody(form({ show_pass_result: { checked: true } }), 3, "save"),
  unticked: collectReviewBody(form({ show_pass_result: { checked: false } }), 3, "save"),
}));
"""


@pytest.fixture(scope="module")
def js_bodies() -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is not installed; the JavaScript half needs it")
    cases = {
        "catalog": json.dumps(SCRIPT_TEXT),
        "module": (STATIC_DIR / "js" / "review_form.js").as_uri(),
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


def test_js_leaves_the_toggle_out_when_the_box_is_not_on_the_page(js_bodies):
    # Left out = "keep" for the orchestrator: a job of a prova without
    # results never has its choice changed by a save.
    assert "show_pass_result" not in js_bodies["without"]
    # The other fields are still there, as before.
    assert js_bodies["without"]["car_number"] == 26
    assert js_bodies["without"]["show_pass"] is True


def test_js_sends_the_toggle_as_ticked(js_bodies):
    assert js_bodies["ticked"]["show_pass_result"] is True
    assert js_bodies["unticked"]["show_pass_result"] is False
