"""Checking `seconds_before_end` in a prova's `speed` block (LIVESTACK-68).

What this module is for: "Aceleração e Velocidade" shows the run's top
speed under the clock. Until now it appeared from a frame the reviewer
marked; a prova may now say instead that it always appears N seconds
before the end of the cut clip, with no mark to set:

    "speed": {"resultado": "VEL", "column": "Velocidade", "unit": "km/h",
              "decimals": 1, "seconds_before_end": 3}

N is a number of seconds, more than 0 and at most 30, and may have
decimals (2.5). Without the key the speed comes from the mark, as before.

What these tests prove: good values are read as numbers of seconds, a
missing key means "from the mark", and every bad value is refused with
the exact place of the bad key and a problem code the Settings page
writes in pt-BR (with the numbers it needs for the sentence).

What they do not cover: what the job and the render do with N
(test_speed_before_end.py), and the pt-BR sentences themselves
(tests/review_ui/test_speed_before_end_page.py).
"""

import copy
import json

import pytest

from orchestrator.domain.pass_results_config import ConfigError, parse_config
from tests.orchestrator.avf_data import AVF_CONFIG


def avf_config_with_seconds_before_end(value) -> str:
    """The brief's AVF configuration with `seconds_before_end` set to `value`, as typed text.

    The text is built by hand for the values JSON cannot hold in Python
    (NaN), so they reach the parser exactly as an admin could type them.
    """
    config = copy.deepcopy(AVF_CONFIG)
    config["provas"]["AVF"]["speed"]["seconds_before_end"] = value
    return json.dumps(config, ensure_ascii=False)


# Each row: a value the admin may type, the seconds it means, and why the row exists.
GOOD_VALUES = [
    # The brief's example: the speed for the last 3 seconds.
    pytest.param(3, 3.0, id="whole-seconds"),
    # Decimals are allowed: two and a half seconds.
    pytest.param(2.5, 2.5, id="decimal"),
    # The smallest sensible value: a tenth of a second is still more than 0.
    pytest.param(0.1, 0.1, id="a-tenth"),
    # The largest allowed: 30 seconds (a clip is rarely longer than that).
    pytest.param(30, 30.0, id="the-maximum"),
    # 30.0 written with a decimal point is the same maximum.
    pytest.param(30.0, 30.0, id="the-maximum-as-decimal"),
]


@pytest.mark.parametrize(("typed", "seconds"), GOOD_VALUES)
def test_a_good_value_is_read_as_seconds_before_the_end(typed, seconds):
    # Arrange: the AVF configuration with this value of seconds_before_end.
    text = avf_config_with_seconds_before_end(typed)

    # Act: check and read it, as a save does.
    speed = parse_config(text).provas["AVF"].speed

    # Assert: the number of seconds, as a float, and the rest unchanged.
    assert speed.seconds_before_end == seconds, f"{typed!r} must mean {seconds} s before the end"
    assert isinstance(speed.seconds_before_end, float), "always kept as a float"
    assert (speed.resultado, speed.column) == ("VEL", "Velocidade"), "the rest is read as before"


def test_without_the_key_the_speed_comes_from_the_mark():
    # Arrange: the brief's AVF configuration as it was (no seconds_before_end).
    text = json.dumps(AVF_CONFIG, ensure_ascii=False)

    # Act: read it.
    speed = parse_config(text).provas["AVF"].speed

    # Assert: no N, which means the reviewer's mark, exactly as before.
    assert speed.seconds_before_end is None, "absent means the speed mark is used"


# Each row: a bad value, the problem code, its extra details, and why the row exists.
BAD_VALUES = [
    # 0 seconds before the end would never show the speed at all.
    pytest.param(0, "more_than_and_at_most", {"more_than": 0, "maximum": 30}, id="zero"),
    # A negative number of seconds makes no sense.
    pytest.param(-3, "more_than_and_at_most", {"more_than": 0, "maximum": 30}, id="negative"),
    # Just over the maximum.
    pytest.param(30.5, "more_than_and_at_most", {"more_than": 0, "maximum": 30}, id="over-30"),
    # A number written in quotes is text, not a number.
    pytest.param("3", "wrong_type", {"expected": "number"}, id="text"),
    # `true` is not a number of seconds (Python would read it as 1).
    pytest.param(True, "wrong_type", {"expected": "number"}, id="boolean"),
    # A list or an object is not a number either.
    pytest.param([3], "wrong_type", {"expected": "number"}, id="list"),
    # null is not "no value": leaving the key out is.
    pytest.param(None, "wrong_type", {"expected": "number"}, id="null"),
    # Python's json reads NaN and Infinity, which no clip can use.
    pytest.param(float("nan"), "wrong_type", {"expected": "number"}, id="not-a-number"),
    pytest.param(float("inf"), "wrong_type", {"expected": "number"}, id="infinity"),
]


@pytest.mark.parametrize(("typed", "problem", "params"), BAD_VALUES)
def test_a_bad_value_is_refused_with_its_place_and_a_clear_problem(typed, problem, params):
    # Arrange: the AVF configuration with the bad value.
    text = avf_config_with_seconds_before_end(typed)

    # Act: try to read it, as a save does.
    with pytest.raises(ConfigError) as caught:
        parse_config(text)

    # Assert: refused, pointing at the key, with the code and numbers the page writes.
    details = caught.value.details()
    assert details["path"] == "provas.AVF.speed.seconds_before_end", "the error must point at N"
    assert details["problem"] == problem, f"the problem code must be {problem}"
    for name, value in params.items():
        assert details[name] == value, f"the details must carry {name}={value!r} for the page"


def test_the_key_belongs_to_the_speed_block_only():
    # Arrange: seconds_before_end typed in the clock block by mistake.
    config = copy.deepcopy(AVF_CONFIG)
    config["provas"]["AVF"]["clock"]["seconds_before_end"] = 3

    # Act: try to read it.
    with pytest.raises(ConfigError) as caught:
        parse_config(json.dumps(config))

    # Assert: an unknown key there, so the typo is never silently ignored.
    details = caught.value.details()
    assert details["path"] == "provas.AVF.clock.seconds_before_end", "pointing at the clock block"
    assert details["problem"] == "unknown_key", "the clock block has no such key"
