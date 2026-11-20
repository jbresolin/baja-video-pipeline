"""Where the top speed starts when it is shown N seconds before the clip's end (LIVESTACK-68).

What this module is for: a prova's `speed` block may say
`"seconds_before_end": 3`. The top speed under the clock is then shown for
the last 3 seconds of the cut clip, with no mark to set. The orchestrator
works out where that is, in raw-video seconds, and hands it to the worker
as if it were a mark; the job page draws the same moment as a green line
on the cut bar and in its overlay preview, from the form's cut-out before
a save. Python (shared/pass_clock.py) and the browser
(review_ui/static/js/pass_clock.js) must agree, or the preview would
mislead the reviewer.

The rule: the clip ends at the cut-out (or at the end of the video when no
cut-out is set); the start is that end minus N, counted in whole
microseconds and rounded down to the millisecond like the marks, and never
before the cut-in, so a clip shorter than N shows the speed throughout.

What these tests prove: both rules give the start the file expects for
every case in the `speed_before_end_cases` of
tests/fixtures/pass_clock/vectors.json: from a cut-out, from the video's
duration, clamped to the cut-in, with no end at all, decimal values of N,
and floating-point traps.

What they do not cover: the job, the render task and the page that use
the rule (tests/orchestrator/test_speed_before_end.py and
tests/review_ui/test_speed_before_end_*.py).
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from review_ui.templating import STATIC_DIR
from shared.pass_clock import speed_start_before_end

VECTORS_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "pass_clock" / "vectors.json"
VECTORS = json.loads(VECTORS_PATH.read_text(encoding="utf-8"))["speed_before_end_cases"]


def case_id(case: dict) -> str:
    """The test id of a vector: its "why"."""
    return case["why"]


@pytest.mark.parametrize("case", VECTORS, ids=case_id)
def test_python_puts_the_speed_start_where_the_file_says(case):
    # Arrange: the clip's cuts, the video's length and N, from the file.
    cuts = {
        "cut_in_s": case["cut_in_s"],
        "cut_out_s": case["cut_out_s"],
        "duration_s": case["duration_s"],
    }

    # Act: ask the Python rule where the speed starts.
    start = speed_start_before_end(**cuts, seconds_before_end=case["seconds_before_end"])

    # Assert: exactly the start the file expects (None: no end known, no speed).
    assert start == case["start_s"], f"Python must start at {case['start_s']} for: {case['why']}"


def test_the_python_start_is_a_whole_number_of_milliseconds_when_the_cuts_are():
    # Arrange: a clip cut on whole milliseconds, as the job page writes cuts.
    cuts = {"cut_in_s": 0.25, "cut_out_s": 9.871, "duration_s": 12.0}

    # Act: 3 s before its end.
    start = speed_start_before_end(**cuts, seconds_before_end=3)

    # Assert: 6.871 s, which a worker's microsecond rule reads back as 6 871 000 µs.
    assert start == 6.871, "the start must be the cut-out minus 3 s"
    assert round(start * 1_000_000) == 6_871_000, "and a whole number of milliseconds"


# The JavaScript rule, run in Node over the same cases: one line of JSON
# out, the start for every case in order (null for no start).
NODE_SCRIPT = """
const { speedStartBeforeEnd } = await import(process.env.MODULE);
const cases = JSON.parse(process.env.CASES);
console.log(JSON.stringify(cases.map((c) => speedStartBeforeEnd({
  cutIn: c.cut_in_s, cutOut: c.cut_out_s, duration: c.duration_s,
  secondsBeforeEnd: c.seconds_before_end,
}))));
"""


@pytest.fixture(scope="module")
def javascript_answers() -> list:
    """What review_ui/static/js/pass_clock.js answers for every vector."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is not installed; the JavaScript half needs it")
    finished = subprocess.run(
        [node, "--input-type=module", "-e", NODE_SCRIPT],
        env={
            "MODULE": (STATIC_DIR / "js" / "pass_clock.js").as_uri(),
            "CASES": json.dumps(VECTORS),
            "PATH": "",
        },
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert finished.returncode == 0, finished.stderr
    return json.loads(finished.stdout)


@pytest.mark.parametrize("index", range(len(VECTORS)), ids=[case_id(case) for case in VECTORS])
def test_javascript_puts_the_speed_start_where_the_file_says(javascript_answers, index):
    # Arrange: the case from the file.
    case = VECTORS[index]

    # Act: read what the browser's rule answered for it.
    start = javascript_answers[index]

    # Assert: the same start as the file (and so as Python).
    assert start == case["start_s"], f"JavaScript must start at {case['start_s']}: {case['why']}"
