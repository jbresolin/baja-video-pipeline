"""The result line for rows taken from the 2024 Sudeste results (orchestrator/domain/pass_text.py).

Derived from tiagomartines11/baja-sae-brasil-online,
baja-php/tests/results-snapshots/24SE/24SE_SUS.json and 24SE_MAN.json:
snapshots of the old results pages, one object per row keyed by column
header. The rows below are those snapshots' values (display HTML exactly
as there) rewritten in the shape resultado.php answers today (`columns`
plus `rows[].cells[]`, as in tests/fixtures/results_api/). The `raw`
values are what the API makes of that HTML: a list per run, a number when
the run is one number, else its text without the icons.

    24SE_SUS, car 1: Tempo[s] "42.5<br> <b>37.81</b>", Obstáculos "<b>8</b><br> <b>8</b>"
    24SE_MAN, car 1: Tempo "---<br> 37.26 (2<img class="cone">/1<img class="gate">)",
                     Tempo Corrigido "---<br> <b>57.26</b>"
"""

import json

import pytest

from orchestrator.domain.pass_results_config import parse_config
from orchestrator.domain.pass_text import build_pass_text
from orchestrator.domain.results_table import parse_table
from shared.models import PassResultProblem
from tests.orchestrator.pass_results_data import MANOBRABILIDADE_CONFIG

POLI = {"number": 1, "name": "Poli", "school": "Escola Politécnica da USP", "state": "SP"}


def answer(resultado_id: str, cells: dict[str, dict]) -> dict:
    """A resultado.php answer with car 1's row: list columns named as `cells`' keys."""
    return {
        "event_id": "24SE",
        "resultado_id": resultado_id,
        "columns": [
            {"header": "#", "kind": "team_number", "ranking": False},
            *({"header": header, "kind": "list", "ranking": False} for header in cells),
        ],
        "rows": [
            {
                "position": 5,
                "team": dict(POLI, disqualified=False),
                "cells": [{"raw": 1, "display": 1}, *cells.values()],
            }
        ],
    }


# 24SE_SUS, car 1: two runs; each run's time and obstacles passed.
SUS_TABLE = answer(
    "24SE_SUS",
    {
        "Tempo[s]": {"raw": [42.5, 37.81], "display": "42.5<br> <b>37.81</b>"},
        "Obstáculos": {"raw": [8, 8], "display": "<b>8</b><br> <b>8</b>"},
    },
)
# The design.md example's Suspensão entry.
SUS_CONFIG = {
    "provas": {
        "SUS": {
            "result": {
                "column": "Tempo[s]",
                "unit": "s",
                "decimals": 2,
                "extra": [{"column": "Obstáculos", "unit": "obstáculos"}],
            }
        }
    }
}

# 24SE_MAN, car 1: no first run ("---"), a second with 2 cones and 1 gate.
MAN_TABLE = answer(
    "24SE_MAN",
    {
        "Tempo": {
            "raw": ["---", "37.26 (2 /1 )"],
            "display": '---<br> 37.26 (2<img class="cone">/1<img class="gate">)',
        },
        "Tempo Corrigido": {"raw": ["---", 57.26], "display": "---<br> <b>57.26</b>"},
    },
)


def build(table_body: dict, config: dict, prova_id: str, passagem: int):
    parsed = parse_config(json.dumps(config, ensure_ascii=False))
    return build_pass_text(
        parse_table(table_body), parsed.provas[prova_id], parsed.penalties, 1, passagem
    )


# --- Suspensão: the extra column ---


@pytest.mark.parametrize(
    ("passagem", "expected"),
    [(1, "42,50 s · 8 obstáculos"), (2, "37,81 s · 8 obstáculos")],
)
def test_suspensao_shows_the_time_then_the_obstacles(passagem, expected):
    outcome = build(SUS_TABLE, SUS_CONFIG, "SUS", passagem)
    assert (outcome.text, outcome.problem) == (expected, None)


def test_an_extra_without_a_value_for_the_run_is_left_out():
    # The obstacles of run 2 not typed in yet: the time alone is shown.
    table = json.loads(json.dumps(SUS_TABLE))
    table["rows"][0]["cells"][2] = {"raw": [8, "-"], "display": "<b>8</b><br> -"}
    assert build(table, SUS_CONFIG, "SUS", 2).text == "37,81 s"
    assert build(table, SUS_CONFIG, "SUS", 1).text == "42,50 s · 8 obstáculos"


def test_extras_come_after_the_corrected_value():
    config = json.loads(json.dumps(MANOBRABILIDADE_CONFIG))
    config["provas"]["MAN"]["result"]["extra"] = [{"column": "Tempo Corrigido", "decimals": 1}]
    assert build(MAN_TABLE, config, "MAN", 2).text == (
        "37 s · 2 cones, 1 saída de pista → 57 s · 57,3"
    )


# --- Manobrabilidade: "---" is a run not done ---


def test_a_run_shown_as_three_dashes_has_no_result_yet():
    outcome = build(MAN_TABLE, MANOBRABILIDADE_CONFIG, "MAN", 1)
    assert (outcome.text, outcome.problem) == (None, PassResultProblem.NO_RESULT_YET)


def test_the_run_after_it_keeps_its_own_time_and_penalties():
    # The display's "---" line still pairs with run 1, so run 2's
    # penalties are read from run 2's line.
    outcome = build(MAN_TABLE, MANOBRABILIDADE_CONFIG, "MAN", 2)
    assert outcome.text == "37 s · 2 cones, 1 saída de pista → 57 s"
