"""The real results data and the configuration the pass-results tests use (LIVESTACK-47).

tests/fixtures/results_api/ holds real answers of the Baja results API
for the 2026 national Manobrabilidade ("26BR_MAN"): two runs per car in
the list columns "Tempo" and "Tempo Corrigido". This module loads them and
sets up a test orchestrator so its fake results API serves them, with a
synced prova "Manobrabilidade" whose API id is "MAN".
"""

import copy
import json
from pathlib import Path

from orchestrator.testing.scenarios import hold_raw, make_proxy, receive_job, save_review_fields
from shared.models import WorkerRole

FIXTURES = Path(__file__).parent.parent / "fixtures" / "results_api"
RESULTADO_ID = "26BR_MAN"

# The brief's example configuration for Manobrabilidade.
MANOBRABILIDADE_CONFIG = {
    "penalties": {
        "cone": {"one": "cone", "other": "cones"},
        "gate": {"one": "saída de pista", "other": "saídas de pista"},
    },
    "provas": {
        "MAN": {
            "resultado": "MAN",
            "runs": "list_columns",
            "show_by_default": True,
            "result": {
                "column": "Tempo",
                "unit": "s",
                "decimals": 0,
                "penalties": True,
                "corrected_column": "Tempo Corrigido",
            },
            "clock": {"column": "Tempo", "decimals": 1},
        }
    },
}
MANOBRABILIDADE_CONFIG_TEXT = json.dumps(MANOBRABILIDADE_CONFIG, ensure_ascii=False, indent=2)
# How the results API lists the prova in evento.php (the fake adds it to 26BR).
MAN_PROVA = {
    "id": "MAN",
    "name": "Manobrabilidade",
    "type": "normal",
    "status": "final",
    "tournament": False,
    "lap_counted": False,
    "live_url": None,
}


def man_table(display: bool = True) -> dict:
    """A fresh copy of the real answer (with display=1, or without it)."""
    name = "26BR_MAN.display.json" if display else "26BR_MAN.json"
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def car_row(table: dict, car_number: int) -> dict:
    return next(row for row in table["rows"] if row["team"]["number"] == car_number)


def set_up_manobrabilidade(harness, *, config_text: str = MANOBRABILIDADE_CONFIG_TEXT) -> None:
    """Event 26BR synced (with Manobrabilidade), the real table served, the config saved."""
    harness.results_api.provas_of().append(copy.deepcopy(MAN_PROVA))
    harness.results_api.tables[RESULTADO_ID] = man_table()
    response = harness.client.patch(
        "/admin/settings", json={"event_id": "26BR"}, auth=harness.admin_auth
    )
    assert response.status_code == 200, response.text
    save_config(harness, config_text)


def save_config(harness, config_text: str):
    return harness.client.put(
        "/admin/pass-results/config",
        json={"config_text": config_text},
        auth=harness.admin_auth,
    )


def job_in_review(harness, car=26, passagem=2, *, worker=None) -> str:
    """A Manobrabilidade job with car and passagem saved, waiting in the review queue.

    A worker (a new master by default) holds the raw and makes the proxy,
    as in real life; that is what brings a job to the review queue.
    """
    worker = worker or harness.enroll(WorkerRole.MASTER, "master")
    job_id = receive_job(harness)
    hold_raw(harness, worker, job_id)
    make_proxy(harness, worker, job_id)
    save_review_fields(harness, job_id, prova="Manobrabilidade", car_number=car, passagem=passagem)
    return job_id
