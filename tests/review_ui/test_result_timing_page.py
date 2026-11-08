"""With a clock, the result appears when the clock stops: the job page's side (LIVESTACK-53).

The story: a clip with a running clock shows its result pill only from the
moment the clock freezes on the official time. If the reviewer's cut ends
before that moment, the clip would never show its result, so the clock
block warns: "O clipe termina antes do cronômetro parar; o resultado não
vai aparecer. Estenda o fim do corte." It never blocks a save.

The page decides that live from its own fields (js/clock_late.js), so the
server only puts what the script needs on the blocks: the official time
on the clock block and "there is a result line" on the result block. The
script's decision runs in Node at the end; the rule it uses
(clipEndsBeforeFreeze) is checked against the shared vectors in
tests/shared/test_pass_clock_rule.py.

Setup (conftest.py): `ui` is a signed-in browser against a fake
orchestrator; sample_data.pass_clock_job() is car 26's second
Manobrabilidade run, official time 66,0, result "66 s · 4 cones → 86 s".
"""

import json
import shutil
import subprocess

import pytest

from review_ui.templating import STATIC_DIR
from tests.review_ui.sample_data import pass_clock, pass_clock_job, pass_result

WARNING = (
    "O clipe termina antes do cronômetro parar; o resultado não vai aparecer. "
    "Estenda o fim do corte."
)


def job_page(ui, fake_orchestrator, job) -> str:
    fake_orchestrator.add_job(job)
    return ui.get(f"/review/jobs/{job.id}").text


def test_the_clock_block_carries_the_official_time_and_a_hidden_warning(ui, fake_orchestrator):
    page = job_page(ui, fake_orchestrator, pass_clock_job())

    # What js/clock_late.js reads: 660 units with one decimal (66,0).
    assert 'data-official-units="660" data-decimals="1"' in page
    # The sentence is on the page, in pt-BR, hidden until the script shows it.
    assert f'id="pass-clock-late" hidden>{WARNING}</p>' in page


def test_the_result_block_says_whether_it_has_a_line(ui, fake_orchestrator):
    with_line = job_page(ui, fake_orchestrator, pass_clock_job())
    without_line = job_page(
        ui,
        fake_orchestrator,
        pass_clock_job("job-clock-2", pass_result=pass_result(text=None)),
    )

    assert 'id="pass-result" data-has-text>' in with_line
    assert 'id="pass-result">' in without_line


def test_without_an_official_time_the_block_has_no_units(ui, fake_orchestrator):
    # No result published yet: nothing to compare the cut with, no warning.
    clock = pass_clock(official_units=None, official_text=None, render=None)
    page = job_page(ui, fake_orchestrator, pass_clock_job(pass_clock=clock))

    assert "data-official-units" not in page


# --- The script's decision, in Node ---

NODE_SCRIPT = """
const { resultNeverAppears } = await import(process.env.MODULE);
// Car 26's second run: largada 12.345 s, the clock stops at 66,0, i.e. at
// raw 78.345 s. Each case changes one thing from this.
const base = { officialUnits: 660, decimals: 1, largada: 12.345, cutOut: 90.0,
               showClock: true, showResult: true };
const cases = {
  ends_after_the_freeze: {},
  ends_before_the_freeze: { cutOut: 70.0 },
  ends_exactly_at_the_freeze: { cutOut: 78.345 },
  clock_turned_off: { cutOut: 70.0, showClock: false },
  result_turned_off: { cutOut: 70.0, showResult: false },
  no_largada_yet: { cutOut: 70.0, largada: null },
  no_end_of_cut_yet: { cutOut: null },
  no_official_time_yet: { cutOut: 70.0, officialUnits: null },
};
const out = {};
for (const [name, change] of Object.entries(cases)) {
  out[name] = resultNeverAppears({ ...base, ...change });
}
console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def decisions() -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is not installed; the JavaScript half needs it")
    finished = subprocess.run(
        [node, "--input-type=module", "-e", NODE_SCRIPT],
        env={"MODULE": (STATIC_DIR / "js" / "clock_late.js").as_uri(), "PATH": ""},
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert finished.returncode == 0, finished.stderr
    return json.loads(finished.stdout)


def test_js_warns_when_the_cut_ends_before_the_clock_stops(decisions):
    assert decisions["ends_before_the_freeze"] is True
    # The clip has no frame at its cut-out, so ending exactly there is too soon.
    assert decisions["ends_exactly_at_the_freeze"] is True


def test_js_is_quiet_when_the_result_does_appear(decisions):
    assert decisions["ends_after_the_freeze"] is False


def test_js_is_quiet_when_there_is_no_clock_or_no_result_to_wait_for(decisions):
    # Without a clock the result shows the whole clip; without a result
    # there is nothing to miss.
    assert decisions["clock_turned_off"] is False
    assert decisions["result_turned_off"] is False


def test_js_is_quiet_while_something_is_still_missing(decisions):
    assert decisions["no_largada_yet"] is False
    assert decisions["no_end_of_cut_yet"] is False
    assert decisions["no_official_time_yet"] is False
