"""Checking the `speed` block of the pass-results configuration (LIVESTACK-67).

What this module is for: "Aceleração e Velocidade" takes its clock from
one results table (ACE) and the top speed under the clock from another
(VEL). The admin says so in the configuration on the Settings page:

    "AVF": {"resultado": "ACE",
            "clock": {"column": "Aceleração", "decimals": 2, "unit": "s"},
            "speed": {"resultado": "VEL", "column": "Velocidade",
                      "unit": "km/h", "decimals": 1}}

What these tests prove: the brief's example is read with every value; the
defaults (no unit, 0 decimals); and every mistake is refused with the
exact place of the bad key and a problem code the review UI writes in
pt-BR: unknown keys, missing keys, a bad table id, a bad unit, decimals
outside 0–3, and a speed without a clock.

What they do not cover: refusing through the HTTP API, with the old
configuration staying in force (the same code path as every other block:
test_pass_results_settings.py).
"""

import copy
import json

import pytest

from orchestrator.domain.pass_results_config import ConfigError, parse_config
from tests.orchestrator.avf_data import AVF_CONFIG


def refused(document: dict) -> ConfigError:
    """Parse `document` and return the error it must cause."""
    with pytest.raises(ConfigError) as caught:
        parse_config(json.dumps(document, ensure_ascii=False))
    return caught.value


def avf_with_speed(**speed_changes) -> dict:
    """The brief's AVF configuration with its `speed` block changed; None removes a key."""
    config = copy.deepcopy(AVF_CONFIG)
    speed = config["provas"]["AVF"]["speed"]
    for key, value in speed_changes.items():
        if value is None:
            del speed[key]
        else:
            speed[key] = value
    return config


def test_the_briefs_example_is_read_with_every_value():
    # Arrange: the brief's configuration for AVF, as the admin would type it.
    text = json.dumps(AVF_CONFIG, ensure_ascii=False)

    # Act: check and read it, as a save does.
    avf = parse_config(text).provas["AVF"]

    # Assert: the clock from ACE and the speed from VEL, with their units and decimals.
    assert avf.resultado == "ACE", "the clock's table is the prova's own resultado"
    assert (avf.clock.column, avf.clock.decimals, avf.clock.unit) == ("Aceleração", 2, "s")
    speed = avf.speed
    assert speed.resultado == "VEL", "the speed comes from its own table"
    assert (speed.column, speed.unit, speed.decimals) == ("Velocidade", "km/h", 1)


def test_unit_and_decimals_may_be_left_out():
    # Arrange: a speed block with only its table and column.
    config = avf_with_speed(unit=None, decimals=None)

    # Act: read it.
    speed = parse_config(json.dumps(config)).provas["AVF"].speed

    # Assert: no unit and whole numbers, like the result line's defaults.
    assert (speed.unit, speed.decimals) == ("", 0), "defaults: no unit, 0 decimals"


def test_a_prova_without_a_speed_block_has_no_speed():
    # Arrange: the AVF configuration with the speed block taken out.
    config = copy.deepcopy(AVF_CONFIG)
    del config["provas"]["AVF"]["speed"]

    # Act: read it.
    avf = parse_config(json.dumps(config)).provas["AVF"]

    # Assert: still a valid prova (it has a clock), without a speed.
    assert avf.speed is None, "no speed block means no speed"


# Each row: what the admin typed wrong, where the error must point, and its code.
MISTAKES = [
    # A typo in a key must never be ignored silently.
    pytest.param({"colum": "Velocidade"}, "provas.AVF.speed.colum", "unknown_key", id="typo"),
    # Without the table, there is nothing to fetch the speed from.
    pytest.param({"resultado": None}, "provas.AVF.speed.resultado", "missing_key", id="no-table"),
    # Without the column, there is nothing to read in that table.
    pytest.param({"column": None}, "provas.AVF.speed.column", "missing_key", id="no-column"),
    # A table id is 1 to 3 letters or digits, like the prova's own resultado.
    pytest.param({"resultado": "V-L"}, "provas.AVF.speed.resultado", "bad_id", id="bad-id"),
    # And never longer than 3 characters.
    pytest.param({"resultado": "VELO"}, "provas.AVF.speed.resultado", "too_long", id="long-id"),
    # More than 3 decimals would not be a speed anyone reads on air.
    pytest.param({"decimals": 4}, "provas.AVF.speed.decimals", "out_of_range", id="decimals-4"),
    # Negative decimals make no sense.
    pytest.param({"decimals": -1}, "provas.AVF.speed.decimals", "out_of_range", id="decimals-neg"),
    # `true` is not a number of decimals (Python would read it as 1).
    pytest.param({"decimals": True}, "provas.AVF.speed.decimals", "wrong_type", id="decimals-bool"),
    # A unit is text, and short enough to fit the pill.
    pytest.param({"unit": 5}, "provas.AVF.speed.unit", "wrong_type", id="unit-number"),
    pytest.param({"unit": "quilômetros/hora"}, "provas.AVF.speed.unit", "too_long", id="unit-long"),
]


@pytest.mark.parametrize(("speed_changes", "path", "problem"), MISTAKES)
def test_a_mistake_in_the_speed_block_is_refused_with_its_place(speed_changes, path, problem):
    # Arrange: the brief's configuration with one mistake in its speed block.
    config = avf_with_speed(**speed_changes)

    # Act: try to read it, as a save does.
    error = refused(config)

    # Assert: refused, pointing at the bad key, with the code the page translates.
    assert error.details()["path"] == path, "the error must point at the bad key"
    assert error.problem == problem, f"the problem code must be {problem}"


def test_the_speed_block_must_be_an_object():
    # Arrange: a speed written as plain text instead of {…}.
    config = copy.deepcopy(AVF_CONFIG)
    config["provas"]["AVF"]["speed"] = "Velocidade"

    # Act: try to read it.
    error = refused(config)

    # Assert: refused as the wrong kind of value.
    assert (error.details()["path"], error.problem) == ("provas.AVF.speed", "wrong_type")


def test_a_speed_needs_a_clock_to_sit_under():
    # Arrange: a prova with a result line and a speed but no clock.
    config = {
        "provas": {
            "AVF": {
                "resultado": "ACE",
                "result": {"column": "Aceleração", "unit": "s", "decimals": 2},
                "speed": {"resultado": "VEL", "column": "Velocidade"},
            }
        }
    }

    # Act: try to read it.
    error = refused(config)

    # Assert: refused at the speed block: the speed is drawn only under a clock.
    assert error.details()["path"] == "provas.AVF.speed", "the error must point at the speed"
    assert error.problem == "speed_needs_clock", "the page must say the speed needs a clock"


def test_a_speed_alone_still_needs_a_result_or_a_clock():
    # Arrange: a prova with only a speed block.
    config = {"provas": {"AVF": {"speed": {"resultado": "VEL", "column": "Velocidade"}}}}

    # Act: try to read it.
    error = refused(config)

    # Assert: the existing rule answers first: a prova shows a result or a clock.
    assert error.problem == "nothing_to_show", "a prova needs a result or a clock block"
