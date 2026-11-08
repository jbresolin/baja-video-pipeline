"""Checking the pass-results configuration before it is saved (domain/pass_results_config.py).

The admin types a JSON document on the Settings page. A mistake must be
refused with the exact place of the bad key ("provas.MAN.result.decimals")
and a problem code the review UI turns into a pt-BR sentence, and the old
configuration must stay in force (that part is tested through the API in
test_pass_results_settings.py).
"""

import copy
import json

import pytest

from orchestrator.domain.pass_results_config import ConfigError, parse_config
from tests.orchestrator.pass_results_data import MANOBRABILIDADE_CONFIG


def refused(document) -> ConfigError:
    """Parse `document` (a dict, or text as typed) and return the error it must cause."""
    text = document if isinstance(document, str) else json.dumps(document)
    with pytest.raises(ConfigError) as caught:
        parse_config(text)
    return caught.value


def changed(**edit) -> dict:
    """The brief's example with one change inside provas.MAN; a None value removes the key."""
    config = copy.deepcopy(MANOBRABILIDADE_CONFIG)
    target = config["provas"]["MAN"]
    for dotted, value in edit.items():
        *parents, last = dotted.split("__")
        place = target
        for parent in parents:
            place = place[parent]
        if value is None:
            del place[last]
        else:
            place[last] = value
    return config


def test_the_briefs_example_is_accepted_with_every_value_read():
    config = parse_config(json.dumps(MANOBRABILIDADE_CONFIG))
    man = config.provas["MAN"]
    assert (man.resultado, man.runs, man.show_by_default) == ("MAN", "list_columns", True)
    assert man.result.column == "Tempo"
    assert man.result.corrected_column == "Tempo Corrigido"
    assert (man.result.unit, man.result.decimals, man.result.penalties) == ("s", 0, True)
    # The clock block is for the running clock (LIVESTACK-48); read and kept.
    assert (man.clock.column, man.clock.decimals) == ("Tempo", 1)
    assert config.penalties["gate"].for_count(1) == "saída de pista"
    assert config.penalties["gate"].for_count(2) == "saídas de pista"


def test_an_empty_text_means_nothing_is_configured():
    assert parse_config("").provas == {}
    assert parse_config("  \n").provas == {}


def test_defaults_for_the_optional_keys():
    # Only what is required: the resultado defaults to the prova id, runs
    # to list columns, the toggle to on, no unit, no decimals, no penalties.
    config = parse_config('{"provas": {"SUS": {"result": {"column": "Tempo"}}}}')
    sus = config.provas["SUS"]
    assert (sus.resultado, sus.runs, sus.show_by_default) == ("SUS", "list_columns", True)
    assert (sus.result.unit, sus.result.decimals, sus.result.penalties) == ("", 0, False)
    assert sus.result.corrected_column is None and sus.clock is None
    assert sus.result.extra == ()


def test_the_clock_unit_defaults_to_seconds_and_may_be_changed_or_left_empty():
    # LIVESTACK-53: the clock reads "66,0 s". The brief's example has no
    # unit in its clock block, so it gets the default "s".
    assert parse_config(json.dumps(MANOBRABILIDADE_CONFIG)).provas["MAN"].clock.unit == "s"
    # Another unit, or none at all (an empty text).
    assert parse_config(json.dumps(changed(clock__unit="seg"))).provas["MAN"].clock.unit == "seg"
    assert parse_config(json.dumps(changed(clock__unit=""))).provas["MAN"].clock.unit == ""


def test_extra_columns_are_read_in_order_with_their_own_unit_and_decimals():
    config = parse_config(
        json.dumps(
            {
                "provas": {
                    "SUS": {
                        "result": {
                            "column": "Tempo[s]",
                            "extra": [
                                {"column": "Obstáculos", "unit": "obstáculos"},
                                {"column": "Velocidade", "unit": "km/h", "decimals": 1},
                            ],
                        }
                    }
                }
            }
        )
    )
    extra = config.provas["SUS"].result.extra
    assert [(item.column, item.unit, item.decimals) for item in extra] == [
        ("Obstáculos", "obstáculos", 0),
        ("Velocidade", "km/h", 1),
    ]


def test_keys_starting_with_an_underscore_are_comments():
    # JSON has no comments, so "_comment" keys are allowed (and ignored) anywhere.
    config = changed(_comment="tempo bruto, corrigido depois")
    config["_comment"] = "configuração do nacional"
    config["provas"]["_note"] = "só MAN por enquanto"
    assert list(parse_config(json.dumps(config)).provas) == ["MAN"]


def test_text_that_is_not_json_says_where():
    error = refused('{"provas": {"MAN": }}')
    assert error.problem == "not_json"
    assert (error.params["line"], error.params["column"]) == (1, 20)


@pytest.mark.parametrize(
    ("edit", "path", "problem"),
    [
        # A typo in a key must not be silently ignored.
        ({"show_by_defalt": True}, "provas.MAN.show_by_defalt", "unknown_key"),
        ({"result__colunm": "Tempo"}, "provas.MAN.result.colunm", "unknown_key"),
        ({"result__column": None}, "provas.MAN.result.column", "missing_key"),
        ({"result__decimals": 4}, "provas.MAN.result.decimals", "out_of_range"),
        ({"result__decimals": 1.5}, "provas.MAN.result.decimals", "wrong_type"),
        ({"result__decimals": True}, "provas.MAN.result.decimals", "wrong_type"),
        ({"result__penalties": "sim"}, "provas.MAN.result.penalties", "wrong_type"),
        ({"result__column": "  "}, "provas.MAN.result.column", "empty_text"),
        ({"result__unit": "segundos!!!!!"}, "provas.MAN.result.unit", "too_long"),
        ({"runs": "columns"}, "provas.MAN.runs", "not_one_of"),
        ({"resultado": "MANO"}, "provas.MAN.resultado", "too_long"),
        ({"resultado": "M-N"}, "provas.MAN.resultado", "bad_id"),
        ({"show_by_default": "true"}, "provas.MAN.show_by_default", "wrong_type"),
        ({"clock__decimals": -1}, "provas.MAN.clock.decimals", "out_of_range"),
        # The clock's unit (LIVESTACK-53) is checked like the result's.
        ({"clock__unit": "segundos!!!!!"}, "provas.MAN.clock.unit", "too_long"),
        ({"clock__unit": 1}, "provas.MAN.clock.unit", "wrong_type"),
        ({"clock__units": "s"}, "provas.MAN.clock.units", "unknown_key"),
        ({"result": None, "clock": None}, "provas.MAN", "nothing_to_show"),
        ({"result": []}, "provas.MAN.result", "wrong_type"),
        # Extra columns: a list of objects; the item's place is in brackets.
        ({"result__extra": {"column": "Obstáculos"}}, "provas.MAN.result.extra", "wrong_type"),
        ({"result__extra": [{"unit": "x"}]}, "provas.MAN.result.extra[0].column", "missing_key"),
        (
            {"result__extra": [{"column": "A"}, {"column": "B", "decimal": 1}]},
            "provas.MAN.result.extra[1].decimal",
            "unknown_key",
        ),
        (
            {"result__extra": [{"column": "A", "decimals": 5}]},
            "provas.MAN.result.extra[0].decimals",
            "out_of_range",
        ),
        ({"result__extra": [{"column": "A"}] * 5}, "provas.MAN.result.extra", "too_many"),
    ],
)
def test_each_mistake_is_refused_with_its_path_and_problem(edit, path, problem):
    error = refused(changed(**edit))
    assert error.details()["path"] == path
    assert error.problem == problem


def test_mistakes_outside_the_prova_say_where_too():
    config = copy.deepcopy(MANOBRABILIDADE_CONFIG)
    config["penalties"]["cone"] = {"one": "cone"}
    assert refused(config).details()["path"] == "penalties.cone.other"

    config = copy.deepcopy(MANOBRABILIDADE_CONFIG)
    config["penalties"]["cone with space"] = {"one": "x", "other": "y"}
    assert refused(config).problem == "bad_name"

    config = copy.deepcopy(MANOBRABILIDADE_CONFIG)
    config["provas"]["MANOBRA"] = config["provas"].pop("MAN")
    assert refused(config).details() == {"path": "provas.MANOBRA", "problem": "bad_id"}

    assert refused({"penalties": {}}).details() == {"path": "provas", "problem": "missing_key"}
    assert refused([]).problem == "wrong_type"


def test_the_details_carry_what_the_ui_needs_to_explain_the_problem():
    # The review UI writes "deve estar entre 0 e 3" from these values.
    details = refused(changed(result__decimals=9)).details()
    assert details == {
        "path": "provas.MAN.result.decimals",
        "problem": "out_of_range",
        "minimum": 0,
        "maximum": 3,
    }
    assert refused(changed(runs="x")).details()["choices"] == ["list_columns", "rows"]
