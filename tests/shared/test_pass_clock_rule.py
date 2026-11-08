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

LIVESTACK-53 added two questions to the same file: is the result pill
shown at that moment (`result_shown`: with a clock, only from the freeze
on), and does a clip cut at `cut_out_s` end before the clock stops
(`late_cases`: then the result never appears and the job page warns).
"""

import json
import shutil
import subprocess
from decimal import Decimal
from pathlib import Path

import pytest

from review_ui.templating import STATIC_DIR
from shared.pass_clock import (
    clip_ends_before_freeze,
    clock_text,
    clock_text_at,
    clock_units,
    elapsed_microseconds,
    freeze_elapsed_microseconds,
    official_units,
    result_shown_at,
)

VECTORS_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "pass_clock" / "vectors.json"
VECTOR_FILE = json.loads(VECTORS_PATH.read_text(encoding="utf-8"))
VECTORS = VECTOR_FILE["cases"]
LATE_VECTORS = VECTOR_FILE["late_cases"]


def case_id(case: dict) -> str:
    return case["why"]


@pytest.mark.parametrize("case", VECTORS, ids=case_id)
def test_python_rule_matches_every_vector(case):
    shown = clock_text_at(
        case["time_s"], case["largada_s"], case["official_units"], case["decimals"]
    )
    assert shown == case["text"]


@pytest.mark.parametrize("case", VECTORS, ids=case_id)
def test_python_result_timing_matches_every_vector(case):
    shown = result_shown_at(
        case["time_s"], case["largada_s"], case["official_units"], case["decimals"]
    )
    assert shown is case["result_shown"]


@pytest.mark.parametrize("case", LATE_VECTORS, ids=case_id)
def test_python_says_when_the_clip_ends_before_the_clock_stops(case):
    late = clip_ends_before_freeze(
        case["cut_out_s"], case["largada_s"], case["official_units"], case["decimals"]
    )
    assert late is case["late"]


# The JavaScript rule, run in Node over the same file: one line of JSON
# out, with for every case, in order, the clock's text and whether the
# result is shown, and for every late case whether the clip ends too soon.
NODE_SCRIPT = """
const { clockTextAt, resultShownAt, clipEndsBeforeFreeze } = await import(process.env.MODULE);
const cases = JSON.parse(process.env.CASES);
const lateCases = JSON.parse(process.env.LATE_CASES);
console.log(JSON.stringify({
  texts: cases.map((c) => clockTextAt(c.time_s, c.largada_s, c.official_units, c.decimals)),
  results: cases.map((c) => resultShownAt(c.time_s, c.largada_s, c.official_units, c.decimals)),
  late: lateCases.map((c) =>
    clipEndsBeforeFreeze(c.cut_out_s, c.largada_s, c.official_units, c.decimals)),
}));
"""


@pytest.fixture(scope="module")
def javascript_answers() -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is not installed; the JavaScript half needs it")
    finished = subprocess.run(
        [node, "--input-type=module", "-e", NODE_SCRIPT],
        env={
            "MODULE": (STATIC_DIR / "js" / "pass_clock.js").as_uri(),
            "CASES": json.dumps(VECTORS),
            "LATE_CASES": json.dumps(LATE_VECTORS),
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
def test_javascript_rule_matches_every_vector(javascript_answers, index):
    assert javascript_answers["texts"][index] == VECTORS[index]["text"]


@pytest.mark.parametrize("index", range(len(VECTORS)), ids=[case_id(c) for c in VECTORS])
def test_javascript_result_timing_matches_every_vector(javascript_answers, index):
    assert javascript_answers["results"][index] is VECTORS[index]["result_shown"]


@pytest.mark.parametrize("index", range(len(LATE_VECTORS)), ids=[case_id(c) for c in LATE_VECTORS])
def test_javascript_says_when_the_clip_ends_before_the_clock_stops(javascript_answers, index):
    assert javascript_answers["late"][index] is LATE_VECTORS[index]["late"]


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


def test_the_result_appears_exactly_when_the_clock_shows_the_official_time():
    # 66,0 with one decimal is 66 000 000 µs after the largada. One
    # microsecond before, the clock still shows 65,9 and there is no result.
    assert freeze_elapsed_microseconds(660, 1) == 66_000_000
    assert freeze_elapsed_microseconds(3, 0) == 3_000_000
    assert freeze_elapsed_microseconds(1234, 3) == 1_234_000
    assert clock_units(65_999_999, 660, 1) == 659
    assert clock_units(66_000_000, 660, 1) == 660
