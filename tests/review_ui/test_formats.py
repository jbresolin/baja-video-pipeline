"""Dates and numbers: the server (formatting.py) and the page scripts (js/format.js) agree.

A time is first written by the server and later refreshed by the page
script, so both must produce exactly the same text. Each case below is run
through the Python helper AND the JavaScript helper (in Node), with the same
input and the same expected string. The format is Brazilian (LIVESTACK-39):
05/11/2026 09:23:41, 24-hour clock, São Paulo time, decimal comma.

Node is on GitHub's ubuntu runners (and set up explicitly in ci.yml); on a
machine without it the JavaScript half is skipped, the Python half still runs.
"""

import json
import shutil
import subprocess
from datetime import datetime

import pytest

from review_ui import formatting
from review_ui.catalog import SCRIPT_TEXT
from review_ui.templating import STATIC_DIR


def at(iso: str) -> datetime:
    return datetime.fromisoformat(iso.replace("Z", "+00:00"))


# (UTC instant, what São Paulo's clock said). São Paulo is UTC-3 all year
# (no daylight saving since 2019).
CLOCK_CASES = [
    ("2026-11-05T12:23:41Z", "05/11/2026 09:23:41"),
    # Just before midnight in São Paulo, already the next day in UTC.
    ("2026-01-01T02:59:59Z", "31/12/2025 23:59:59"),
    # Midnight is 00, not 24 (24-hour clock).
    ("2026-11-05T03:00:00Z", "05/11/2026 00:00:00"),
    # The afternoon is 13–23 h, never "PM".
    ("2026-11-05T02:29:09Z", "04/11/2026 23:29:09"),
]

# (moment, now, how long ago). Whole units, rounded down.
AGE_CASES = [
    ("2026-11-05T12:00:00Z", "2026-11-05T12:00:00Z", "há 0 s"),
    ("2026-11-05T12:00:00Z", "2026-11-05T12:00:59.900Z", "há 59 s"),
    ("2026-11-05T12:00:00Z", "2026-11-05T12:01:00Z", "há 1 min"),
    ("2026-11-05T12:00:00Z", "2026-11-05T12:59:59Z", "há 59 min"),
    ("2026-11-05T12:00:00Z", "2026-11-05T15:30:00Z", "há 3 h"),
    ("2026-11-03T12:00:00Z", "2026-11-05T13:00:00Z", "há 2 d"),
    # A clock a little ahead on the server: never "há -2 s".
    ("2026-11-05T12:00:02Z", "2026-11-05T12:00:00Z", "há 0 s"),
]

# (moment, now, what a relative <time> shows): age, then the São Paulo clock.
RELATIVE_CASES = [
    ("2026-11-05T12:18:00Z", "2026-11-05T12:23:41Z", "há 5 min (09:18)"),
    ("2026-11-05T02:29:09Z", "2026-11-05T12:00:00Z", "há 9 h (23:29)"),
]

# (seconds into the video, timecode with milliseconds and a decimal comma).
TIMECODE_CASES = [
    (0, "0:00,000"),
    (62.5, "1:02,500"),
    (9.042, "0:09,042"),
    (600.25, "10:00,250"),
]


# --- Python ---


@pytest.mark.parametrize(("instant", "expected"), CLOCK_CASES)
def test_python_clock(instant, expected):
    assert formatting.clock_text(at(instant)) == expected


@pytest.mark.parametrize(("moment", "now", "expected"), AGE_CASES)
def test_python_age(moment, now, expected):
    assert formatting.age_text(at(moment), at(now)) == expected


@pytest.mark.parametrize(("moment", "now", "expected"), RELATIVE_CASES)
def test_python_relative_time(moment, now, expected):
    assert formatting.relative_time_text(at(moment), at(now)) == expected


@pytest.mark.parametrize(("seconds", "expected"), TIMECODE_CASES)
def test_python_timecode(seconds, expected):
    assert formatting.format_timecode(seconds) == expected


def test_python_numbers_use_a_decimal_comma():
    # File sizes, frame rates and rates: comma, never point.
    assert formatting.format_bytes(1_500_000_000) == "1,5 GB"
    assert formatting.format_bytes(80_000_000) == "80,0 MB"
    assert formatting.short_number(29.97) == "29,97"
    assert formatting.short_number(30.0) == "30"
    assert formatting.format_rate(92.5, "Mbps") == "92,5 Mbps"


# --- JavaScript (the same cases, in Node) ---

# Runs in Node: a stand-in `document` holding the catalog (as base.html
# embeds it), then js/format.js on every case. Prints the results as JSON.
NODE_SCRIPT = """
const input = JSON.parse(process.env.FORMAT_CASES);
globalThis.document = { getElementById: () => ({ textContent: input.catalog }) };
const f = await import(input.formatModule);
const at = (iso) => new Date(iso);
console.log(JSON.stringify({
  clock: input.clock.map(([instant]) => f.clockText(at(instant))),
  age: input.age.map(([moment, now]) => f.ageText(at(now) - at(moment))),
  relative: input.relative.map(([moment, now]) => f.relativeTimeText(at(moment), at(now))),
  timecode: input.timecode.map(([seconds]) => f.formatTimecode(seconds)),
}));
"""


@pytest.fixture(scope="module")
def js_results() -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is not installed; the JavaScript half needs it")
    cases = {
        "catalog": json.dumps(SCRIPT_TEXT),
        "formatModule": (STATIC_DIR / "js" / "format.js").as_uri(),
        "clock": CLOCK_CASES,
        "age": AGE_CASES,
        "relative": RELATIVE_CASES,
        "timecode": TIMECODE_CASES,
    }
    finished = subprocess.run(
        [node, "--input-type=module", "-e", NODE_SCRIPT],
        # TZ: a laptop set to Tokyo must still see São Paulo times.
        env={"FORMAT_CASES": json.dumps(cases), "PATH": "", "TZ": "Asia/Tokyo"},
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert finished.returncode == 0, finished.stderr
    return json.loads(finished.stdout)


def test_js_clock(js_results):
    assert js_results["clock"] == [expected for _, expected in CLOCK_CASES]


def test_js_age(js_results):
    assert js_results["age"] == [expected for _, _, expected in AGE_CASES]


def test_js_relative_time(js_results):
    assert js_results["relative"] == [expected for _, _, expected in RELATIVE_CASES]


def test_js_timecode(js_results):
    assert js_results["timecode"] == [expected for _, expected in TIMECODE_CASES]
