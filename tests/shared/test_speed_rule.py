"""When the top speed under the clock is shown, in Python and in JavaScript (LIVESTACK-67).

What this module is for: "Aceleração e Velocidade" shows the run's top
speed under the clock from the frame where the reviewer marked the car
crossing the speed trap to the end of the clip. The clip (drawn by a
worker: ffmpeg's expression is built from shared/pass_clock.py's numbers)
and the job page's overlay preview (drawn by the browser,
review_ui/static/js/pass_clock.js) must agree on every frame, or the
preview would mislead the reviewer.

What these tests prove: both rules give the expected answer for every
case in the `speed_cases` of tests/fixtures/pass_clock/vectors.json, the
file the clock's rule is already checked against: before, at and after
the mark, frames of 30 and 29.97 fps around it, and floating-point traps.

What they do not cover: the real render, frame by frame
(tests/worker/test_speed_real_ffmpeg.py).
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from review_ui.templating import STATIC_DIR
from shared.pass_clock import speed_shown_at

VECTORS_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "pass_clock" / "vectors.json"
SPEED_VECTORS = json.loads(VECTORS_PATH.read_text(encoding="utf-8"))["speed_cases"]


def case_id(case: dict) -> str:
    """The test id of a vector: its "why"."""
    return case["why"]


@pytest.mark.parametrize("case", SPEED_VECTORS, ids=case_id)
def test_python_says_when_the_speed_is_shown_for_every_vector(case):
    # Arrange: one moment of the raw video and the speed mark, from the file.
    time_s, mark_s = case["time_s"], case["speed_mark_s"]

    # Act: ask the Python rule.
    shown = speed_shown_at(time_s, mark_s)

    # Assert: the answer the file expects.
    assert shown is case["shown"], f"Python must say shown={case['shown']} for: {case['why']}"


# The JavaScript rule, run in Node over the same cases: one line of JSON
# out, whether the speed is shown, for every case in order.
NODE_SCRIPT = """
const { speedShownAt } = await import(process.env.MODULE);
const cases = JSON.parse(process.env.CASES);
console.log(JSON.stringify(cases.map((c) => speedShownAt(c.time_s, c.speed_mark_s))));
"""


@pytest.fixture(scope="module")
def javascript_answers() -> list[bool]:
    """What review_ui/static/js/pass_clock.js answers for every speed vector."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is not installed; the JavaScript half needs it")
    finished = subprocess.run(
        [node, "--input-type=module", "-e", NODE_SCRIPT],
        env={
            "MODULE": (STATIC_DIR / "js" / "pass_clock.js").as_uri(),
            "CASES": json.dumps(SPEED_VECTORS),
            "PATH": "",
        },
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert finished.returncode == 0, finished.stderr
    return json.loads(finished.stdout)


@pytest.mark.parametrize(
    "index", range(len(SPEED_VECTORS)), ids=[case_id(case) for case in SPEED_VECTORS]
)
def test_javascript_says_when_the_speed_is_shown_for_every_vector(javascript_answers, index):
    # Arrange: the case from the file.
    case = SPEED_VECTORS[index]

    # Act: read what the browser's rule answered for it.
    shown = javascript_answers[index]

    # Assert: the same answer as the file (and so as Python).
    assert shown is case["shown"], f"JavaScript must say shown={case['shown']} for: {case['why']}"
