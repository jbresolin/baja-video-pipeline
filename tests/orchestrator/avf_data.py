"""The "Aceleração e Velocidade" data and configuration the speed tests use (LIVESTACK-67).

What this module is for: the AVF prova has two results tables for one run,
ACE (column "Aceleração", seconds: the clock freezes on it) and VEL
(column "Velocidade", km/h: the top speed under the clock). This module
loads the fixtures tests/fixtures/results_api/26BR_ACE.display.json and
26BR_VEL.display.json (derived from real 2026 snapshots, invented names;
see that folder's README) and sets up a test orchestrator so its fake
results API serves them, with a synced prova "Aceleração e Velocidade"
whose API id is "AVF".

The cars in the fixtures, run by run:

    car 1   run 1: 4.354 s, 37.18 km/h   run 2: 4.328 s, 36.49 km/h
    car 2   run 1: 4.225 s, 34.14 km/h   run 2: "-" in both (not run)
    car 4   run 1: 4.764 s, 33.98 km/h   run 2: 4.697 s, 33.2 km/h
    car 7   run 1: 4.321 s, 38.14 km/h   run 2: 4.402 s, 38.68 km/h
"""

import copy
import json
from pathlib import Path

from orchestrator.testing.scenarios import hold_raw, make_proxy, receive_job, save_review_fields
from shared.models import WorkerRole

FIXTURES = Path(__file__).parent.parent / "fixtures" / "results_api"
CLOCK_TABLE = "26BR_ACE"
SPEED_TABLE = "26BR_VEL"
AVF_NAME = "Aceleração e Velocidade"

# The brief's configuration for AVF: the clock from ACE, the speed from VEL.
AVF_CONFIG = {
    "provas": {
        "AVF": {
            "resultado": "ACE",
            "clock": {"column": "Aceleração", "decimals": 2, "unit": "s"},
            "speed": {"resultado": "VEL", "column": "Velocidade", "unit": "km/h", "decimals": 1},
        }
    }
}
AVF_CONFIG_TEXT = json.dumps(AVF_CONFIG, ensure_ascii=False, indent=2)
# How the results API lists the prova in evento.php (the fake adds it to 26BR).
AVF_PROVA = {
    "id": "AVF",
    "name": AVF_NAME,
    "type": "normal",
    "status": "final",
    "tournament": False,
    "lap_counted": False,
    "live_url": None,
}


def avf_table(resultado_id: str) -> dict:
    """A fresh copy of a fixture table: "26BR_ACE" or "26BR_VEL"."""
    path = FIXTURES / f"{resultado_id}.display.json"
    return json.loads(path.read_text(encoding="utf-8"))


def set_run_value(table: dict, car_number: int, run: int, value) -> dict:
    """The table with one run of one car changed, as when judges publish or correct it."""
    row = next(row for row in table["rows"] if row["team"]["number"] == car_number)
    row["cells"][3]["raw"][run - 1] = value
    return table


def set_up_avf(harness, *, config_text: str = AVF_CONFIG_TEXT, serve_speed: bool = True) -> None:
    """Event 26BR synced (with AVF), both tables served, the configuration saved.

    `serve_speed=False` leaves the speed table out of the fake API, as
    before the judges publish it: asking for it fails.
    """
    harness.results_api.provas_of().append(copy.deepcopy(AVF_PROVA))
    harness.results_api.tables[CLOCK_TABLE] = avf_table(CLOCK_TABLE)
    if serve_speed:
        harness.results_api.tables[SPEED_TABLE] = avf_table(SPEED_TABLE)
    response = harness.client.patch(
        "/admin/settings", json={"event_id": "26BR"}, auth=harness.admin_auth
    )
    assert response.status_code == 200, response.text
    saved = harness.client.put(
        "/admin/pass-results/config",
        json={"config_text": config_text},
        auth=harness.admin_auth,
    )
    assert saved.status_code == 200, saved.text


def avf_job_in_review(harness, car: int = 1, passagem: int = 1, *, worker=None) -> str:
    """An AVF job with car and passagem saved, waiting in the review queue.

    A worker (a new master by default) holds the raw and makes the proxy,
    as in real life; that is what brings a job to the review queue.
    """
    worker = worker or harness.enroll(WorkerRole.MASTER, "master")
    job_id = receive_job(harness)
    hold_raw(harness, worker, job_id)
    make_proxy(harness, worker, job_id)
    save_review_fields(harness, job_id, prova=AVF_NAME, car_number=car, passagem=passagem)
    return job_id
