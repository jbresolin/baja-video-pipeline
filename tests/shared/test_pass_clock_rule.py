"""The running clock's rule, in Python and in JavaScript, against the same cases (LIVESTACK-48).

Background for a beginner: the clock in the clip (drawn by a worker with
ffmpeg) and the clock in the job page's preview (drawn by the browser)
must show exactly the same thing at the same moment of the video, or the
preview would mislead the reviewer. The rule lives in one place per
language: shared/pass_clock.py (whose numbers the ffmpeg expression in
shared/ffmpeg_clock.py uses) and review_ui/static/js/pass_clock.js.

One file of test vectors, tests/fixtures/pass_clock/vectors.json, lists
moments (raw time, largada, official time, decimals) and the text the
clock must show. These tests run both rules over every vector: if either
gets one wrong, or they disagree, a test fails and names the case.
"""

import json
import shutil
import subprocess
from decimal import Decimal
from pathlib import Path

import pytest

from review_ui.templating import STATIC_DIR
from shared.pass_clock import (
    clock_text,
    clock_text_at,
    clock_units,
    elapsed_microseconds,
    official_units,
)

VECTORS_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "pass_clock" / "vectors.json"
VECTORS = json.loads(VECTORS_PATH.read_text(encoding="utf-8"))["cases"]


def case_id(case: dict) -> str:
    return case["why"]


@pytest.mark.parametrize("case", VECTORS, ids=case_id)
def test_python_rule_matches_every_vector(case):
    shown = clock_text_at(
        case["time_s"], case["largada_s"], case["official_units"], case["decimals"]
    )
    assert shown == case["text"]


# The JavaScript rule, run in Node over the same file: one line of JSON out
# per case, in order.
NODE_SCRIPT = """
const { clockTextAt } = await import(process.env.MODULE);
const cases = JSON.parse(process.env.CASES);
console.log(JSON.stringify(cases.map((c) =>
  clockTextAt(c.time_s, c.largada_s, c.official_units, c.decimals))));
"""


@pytest.fixture(scope="module")
def javascript_texts() -> list:
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


@pytest.mark.parametrize("index", range(len(VECTORS)), ids=[case_id(c) for c in VECTORS])
def test_javascript_rule_matches_every_vector(javascript_texts, index):
    assert javascript_texts[index] == VECTORS[index]["text"]


# --- The pieces of the rule, one by one ---


def test_the_official_time_is_rounded_down_into_clock_units():
    # 59.44 s shown with 1 decimal is 59,4 (594 units), not 59,5: the clock
    # never shows a time the car did not reach.
    assert official_units(Decimal("59.44"), 1) == 594
    assert official_units(Decimal("59.45"), 1) == 594
    assert official_units(Decimal("66"), 1) == 660
    assert official_units(Decimal("66"), 0) == 66
    assert official_units(Decimal("1.2349"), 3) == 1234


def test_elapsed_time_is_counted_in_whole_microseconds():
    # Each time is rounded to the microsecond on its own, then subtracted:
    # 1.0 − 0.9 is exactly 100 000 µs, not 99 999.99… as in floating point.
    assert elapsed_microseconds(1.0, 0.9) == 100_000
    assert elapsed_microseconds(0.5, 0.51) == -10_000


def test_the_count_stops_at_the_official_time_and_is_none_before_the_largada():
    assert clock_units(-1, 15, 1) is None
    assert clock_units(0, 15, 1) == 0
    assert clock_units(1_499_999, 15, 1) == 14
    assert clock_units(1_500_000, 15, 1) == 15
    assert clock_units(99_000_000, 15, 1) == 15


def test_the_text_has_a_decimal_comma_and_no_leading_zeros():
    assert clock_text(0, 1) == "0,0"
    assert clock_text(99, 1) == "9,9"
    assert clock_text(100, 1) == "10,0"
    assert clock_text(5, 2) == "0,05"
    assert clock_text(66, 0) == "66"
