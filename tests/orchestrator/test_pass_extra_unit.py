"""An extra column's unit with names by count: "1 obstáculo", "8 obstáculos" (LIVESTACK-48).

Background for a beginner: the result line can end with other columns of
the same run, such as the obstacles passed in the Suspensão
("37,81 s · 8 obstáculos"). Part A only allowed a fixed unit text, so one
obstacle read "1 obstáculos". Now the unit may also be given like a
penalty's names, {"one": "obstáculo", "other": "obstáculos"}: "one" when
the number as shown is exactly 1, "other" for anything else (0, 2, 1,5).
The fixed text still works as before.

The table is car 1's 2024 Sudeste Suspensão row (tests/orchestrator/
test_pass_text_24se.py), with the obstacle counts changed per test.
"""

import json

import pytest

from orchestrator.domain.pass_results_config import ConfigError, PenaltyNames, parse_config
from tests.orchestrator.test_pass_text_24se import SUS_CONFIG, SUS_TABLE, build

NAMES_BY_COUNT = {"one": "obstáculo", "other": "obstáculos"}


def config_with_unit(unit, decimals=0) -> dict:
    config = json.loads(json.dumps(SUS_CONFIG))
    config["provas"]["SUS"]["result"]["extra"] = [
        {"column": "Obstáculos", "unit": unit, "decimals": decimals}
    ]
    return config


def table_with_obstacles(first_run, second_run) -> dict:
    """Car 1's row with these obstacle counts for runs 1 and 2."""
    table = json.loads(json.dumps(SUS_TABLE))
    table["rows"][0]["cells"][2] = {
        "raw": [first_run, second_run],
        "display": f"{first_run}<br> {second_run}",
    }
    return table


def test_the_configuration_reads_names_by_count():
    parsed = parse_config(json.dumps(config_with_unit(NAMES_BY_COUNT)))
    [extra] = parsed.provas["SUS"].result.extra
    assert extra.unit == PenaltyNames(one="obstáculo", other="obstáculos")


def test_a_fixed_unit_text_still_works():
    parsed = parse_config(json.dumps(config_with_unit("obstáculos")))
    assert parsed.provas["SUS"].result.extra[0].unit == "obstáculos"


@pytest.mark.parametrize(
    ("count", "expected_end"),
    [(1, "1 obstáculo"), (8, "8 obstáculos"), (0, "0 obstáculos"), (2, "2 obstáculos")],
)
def test_one_takes_the_singular_and_anything_else_the_plural(count, expected_end):
    table = table_with_obstacles(count, 3)
    outcome = build(table, config_with_unit(NAMES_BY_COUNT), "SUS", 1)
    assert outcome.text == f"42,50 s · {expected_end}"


def test_the_number_as_shown_decides():
    # With one decimal, 1 shows as "1,0": exactly one, the singular; 1.5
    # shows as "1,5", the plural.
    table = table_with_obstacles(1, 1.5)
    config = config_with_unit(NAMES_BY_COUNT, decimals=1)
    assert build(table, config, "SUS", 1).text == "42,50 s · 1,0 obstáculo"
    assert build(table, config, "SUS", 2).text == "37,81 s · 1,5 obstáculos"


@pytest.mark.parametrize(
    ("unit", "path", "problem"),
    [
        # Both names are needed, like a penalty's.
        ({"one": "obstáculo"}, "provas.SUS.result.extra[0].unit.other", "missing_key"),
        (
            {"one": "a", "other": "b", "few": "c"},
            "provas.SUS.result.extra[0].unit.few",
            "unknown_key",
        ),
        ({"one": "", "other": "b"}, "provas.SUS.result.extra[0].unit.one", "empty_text"),
        (3, "provas.SUS.result.extra[0].unit", "wrong_type"),
    ],
)
def test_a_bad_unit_is_refused_with_its_path(unit, path, problem):
    with pytest.raises(ConfigError) as refused:
        parse_config(json.dumps(config_with_unit(unit)))
    assert refused.value.details()["path"] == path
    assert refused.value.problem == problem
