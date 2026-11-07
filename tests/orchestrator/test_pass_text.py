"""The result line built from the real results data (orchestrator/domain/pass_text.py).

These tests call the text builder directly, with the real answer of the
results API for the 2026 Manobrabilidade (tests/fixtures/results_api/),
so no server is involved. The three lines in the brief must come out
exactly, and they are also the texts of the worker's golden images
(tests/worker/overlay_golden.py): what the orchestrator sends is what the
golden images show.
"""

import copy
import json
from decimal import Decimal

import pytest

from orchestrator.domain.pass_results_config import parse_config
from orchestrator.domain.pass_text import build_pass_text, format_number
from orchestrator.domain.results_table import parse_table, penalties_in, run_number
from shared.models import PassResultProblem
from tests.orchestrator.pass_results_data import MANOBRABILIDADE_CONFIG, car_row, man_table
from tests.worker.overlay_golden import RESULT_CLEAN, RESULT_CONES, RESULT_CONES_GATES


def build(table_body: dict, car: int, passagem: int, config: dict = MANOBRABILIDADE_CONFIG):
    """The text for one car and passagem, under `config` (the brief's example by default)."""
    parsed = parse_config(json.dumps(config))
    return build_pass_text(
        parse_table(table_body), parsed.provas["MAN"], parsed.penalties, car, passagem
    )


# --- The brief's examples, from the real data ---


@pytest.mark.parametrize(
    ("car", "passagem", "expected"),
    [
        # Car 26's second run: "66 (4<img class='cone'>)", corrected 86.
        (26, 2, RESULT_CONES),
        # Car 2's first run: 8 cones and 2 gate penalties, corrected 118.
        (2, 1, RESULT_CONES_GATES),
        # Car 26's first run: 59, corrected 59 (no penalties): the time alone.
        (26, 1, RESULT_CLEAN),
    ],
    ids=["cones-only", "cones-and-gates", "clean-run"],
)
def test_the_briefs_examples_come_out_of_the_real_table(car, passagem, expected):
    assert build(man_table(), car, passagem).text == expected


def test_the_brief_wrote_the_same_strings_as_the_golden_images():
    # The brief's words, typed here independently of the golden constants,
    # so a change to either side is caught.
    assert RESULT_CONES == "66 s · 4 cones → 86 s"
    assert RESULT_CONES_GATES == "58 s · 8 cones, 2 saídas de pista → 118 s"
    assert RESULT_CLEAN == "59 s"


def test_every_run_in_the_real_table_gives_a_line_or_no_result():
    # Every car's two runs: a run not done ("-") gives no text and the
    # reason "no result yet"; every other run gives a line starting with
    # its time. Nothing else (no crash, no other problem) is acceptable.
    table = man_table()
    for row in table["rows"]:
        car = row["team"]["number"]
        for passagem, raw in enumerate(row["cells"][3]["raw"], start=1):
            outcome = build(table, car, passagem)
            if raw == "-":
                assert outcome.text is None
                assert outcome.problem == PassResultProblem.NO_RESULT_YET
            else:
                time = str(raw).split()[0]
                assert outcome.text.startswith(f"{time} s"), (car, passagem, outcome)


def test_one_cone_uses_the_singular_name():
    # Car 23's first run: "67 (1<img class='cone'>)", corrected 72.
    assert build(man_table(), 23, 1).text == "67 s · 1 cone → 72 s"


def test_a_run_without_penalties_but_a_different_corrected_value_shows_both():
    # Car 23's second run: 65, corrected 65 → the time alone; change the
    # corrected value to 70 (say a penalty the display does not show yet)
    # and the arrow appears.
    table = man_table()
    assert build(table, 23, 2).text == "65 s"
    car_row(table, 23)["cells"][4]["raw"][1] = 70
    assert build(table, 23, 2).text == "65 s → 70 s"


# --- Values that must never reach the overlay ---


@pytest.mark.parametrize("hidden", ["-", "SPOILERS", "?", None, "", "–"])
def test_no_result_values_give_no_text(hidden):
    # The judges have not entered the run, or spoilers hide it: the job
    # page says "no result yet" and the clip gets no result pill.
    table = man_table()
    car_row(table, 26)["cells"][3]["raw"][1] = hidden
    outcome = build(table, 26, 2)
    assert outcome.text is None
    assert outcome.problem == PassResultProblem.NO_RESULT_YET


def test_a_text_that_is_not_a_time_gives_no_text():
    table = man_table()
    car_row(table, 26)["cells"][3]["raw"][1] = "DNF"
    outcome = build(table, 26, 2)
    assert (outcome.text, outcome.problem) == (None, PassResultProblem.NOT_A_NUMBER)
    assert "DNF" in outcome.detail


def test_a_missing_corrected_value_still_shows_the_time():
    # The time is entered, the corrected time not yet: the time is official.
    table = man_table()
    car_row(table, 26)["cells"][4]["raw"][1] = "-"
    assert build(table, 26, 2).text == "66 s · 4 cones"


def test_a_car_not_in_the_table_a_run_too_many_and_a_wrong_column_say_why():
    table = man_table()
    assert build(table, 999, 1).problem == PassResultProblem.CAR_NOT_IN_TABLE
    # Every car has two runs in this prova: there is no third passagem.
    assert build(table, 26, 3).problem == PassResultProblem.NO_SUCH_RUN
    config = copy.deepcopy(MANOBRABILIDADE_CONFIG)
    config["provas"]["MAN"]["result"]["column"] = "Tempo Total"
    outcome = build(table, 26, 1, config)
    assert outcome.problem == PassResultProblem.COLUMN_MISSING
    assert "Tempo Total" in outcome.detail


# --- Penalties ---


def test_penalties_off_in_the_config_leaves_them_out():
    config = copy.deepcopy(MANOBRABILIDADE_CONFIG)
    config["provas"]["MAN"]["result"]["penalties"] = False
    assert build(man_table(), 2, 1, config).text == "58 s → 118 s"


def test_an_unknown_penalty_icon_is_shown_by_its_class_name_and_reported():
    # A new icon class the configuration has no names for ("flag"): it is
    # not dropped (the count matters), it is shown by its class name.
    table = man_table()
    new_icon = "59<br /> 66 (4<img class='cone'>/1<img class='flag'>)"
    car_row(table, 26)["cells"][3]["display"] = new_icon
    outcome = build(table, 26, 2)
    assert outcome.text == "66 s · 4 cones, 1 flag → 86 s"
    assert outcome.unknown_penalties == ("flag",)


def test_without_display_html_there_are_no_penalties_but_the_times_still_show():
    # The answer without display=1 has no icons, so no penalties can be read.
    assert build(man_table(display=False), 26, 2).text == "66 s → 86 s"


def test_display_lines_that_do_not_pair_up_with_the_runs_are_not_trusted():
    # If the display had a different number of lines than runs, run 2's
    # penalties might be run 1's: none are shown rather than wrong ones.
    table = man_table()
    car_row(table, 26)["cells"][3]["display"] = "66 (4<img class='cone'>)"
    assert build(table, 26, 2).text == "66 s → 86 s"


def test_penalties_are_read_from_one_display_line():
    line = "58 (8<img class='cone'>/2<img class='gate'>)"
    assert penalties_in(line) == [("cone", 8), ("gate", 2)]
    # Struck-through text is not part of the value (as for raw values).
    assert penalties_in("<s>3<img class='cone'></s> 58 (2<img class='cone'>)") == [("cone", 2)]
    assert penalties_in("59") == []


# --- Numbers ---


@pytest.mark.parametrize(
    ("raw", "value"),
    [(59, "59"), ("66 (4 )", "66"), ("58 (8 /2 )", "58"), ("66,5", "66.5"), (12.25, "12.25")],
)
def test_raw_values_are_read_as_numbers(raw, value):
    assert str(run_number(raw).value) == value


@pytest.mark.parametrize(
    ("value", "decimals", "shown"),
    [("66", 0, "66"), ("59.44", 1, "59,4"), ("59.45", 1, "59,5"), ("59", 2, "59,00"),
     ("252.39999999999998", 1, "252,4")],
)  # fmt: skip
def test_numbers_are_written_the_brazilian_way_with_the_configured_decimals(value, decimals, shown):
    assert format_number(Decimal(value), decimals) == shown


def test_the_corrected_value_is_left_out_when_it_looks_the_same_after_rounding():
    # 59.4 and 59.43 both show as "59,4" with one decimal: no arrow.
    table = man_table()
    car_row(table, 26)["cells"][3]["raw"][0] = 59.4
    car_row(table, 26)["cells"][4]["raw"][0] = 59.43
    config = copy.deepcopy(MANOBRABILIDADE_CONFIG)
    config["provas"]["MAN"]["result"]["decimals"] = 1
    assert build(table, 26, 1, config).text == "59,4 s"


# --- One row per run ---


def test_a_prova_with_one_row_per_run_reads_the_cars_nth_row():
    # Some provas give a car one row per run (the API docs allow it). With
    # "runs": "rows", passagem n is the car's n-th row in the answer.
    columns = [{"header": "#", "kind": "team_number"}, {"header": "Tempo", "kind": "formula"}]
    rows = [
        {"team": {"number": 7}, "cells": [{"raw": 7}, {"raw": 61.2, "display": 61.2}]},
        {"team": {"number": 8}, "cells": [{"raw": 8}, {"raw": 70, "display": 70}]},
        {"team": {"number": 7}, "cells": [{"raw": 7}, {"raw": "-", "display": "-"}]},
    ]
    config = {
        "provas": {
            "MAN": {"runs": "rows", "result": {"column": "Tempo", "unit": "s", "decimals": 1}}
        }
    }
    body = {"columns": columns, "rows": rows}
    assert build(body, 7, 1, config).text == "61,2 s"
    assert build(body, 7, 2, config).problem == PassResultProblem.NO_RESULT_YET
    assert build(body, 7, 3, config).problem == PassResultProblem.NO_SUCH_RUN


def test_a_line_longer_than_a_task_allows_is_cut_with_an_ellipsis():
    # Penalty names come from the admin's configuration. Absurdly long ones
    # must not make the render task invalid (it holds at most 300
    # characters): the line is cut, and the overlay shrinks it anyway.
    config = copy.deepcopy(MANOBRABILIDADE_CONFIG)
    long_name = "x" * 60
    config["penalties"] = {
        "cone": {"one": long_name, "other": long_name},
        "gate": {"one": long_name, "other": long_name},
    }
    table = man_table()
    many_icons = "".join(f"1<img class='{kind}'>/" for kind in ("cone", "gate", "a", "b", "c"))
    # Five penalties of 60-character names: well over 300 characters.
    car_row(table, 2)["cells"][3]["display"] = f"58 ({many_icons})<br /> 57"
    for kind in ("a", "b", "c"):
        config["penalties"][kind] = {"one": long_name, "other": long_name}

    text = build(table, 2, 1, config).text

    assert len(text) == 300
    assert text.endswith("…")
