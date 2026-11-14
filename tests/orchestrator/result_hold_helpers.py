"""Building blocks for the "wait for the official result" tests (LIVESTACK-62).

They reuse the real 2026 Manobrabilidade results (tests/fixtures/results_api/)
served by the fake results API: car 26 already has its second run ("66 s ·
4 cones → 86 s"), car 5 has not done its second run yet ("-"), so its
result is what a held render waits for.
"""

from datetime import datetime, timedelta

from orchestrator.domain.pass_results_fetch import poll_due_tables
from tests.orchestrator.pass_results_data import job_in_review, set_up_manobrabilidade
from tests.orchestrator.test_pass_results_render import FEATURES_0_7_0, enroll_master
from tests.orchestrator.test_render_inputs import edit_review

# The Settings' default "Esperar o resultado oficial por até 15 minutos".
DEFAULT_WAIT = timedelta(minutes=15)
# Car 5's second run once the judges enter it (test_pass_results_render.judges_publish):
# 60 s, 2 cones, 70 s corrected.
CAR_5_RUN_2 = ("60 (2 )", 70, "60 (2<img class='cone'>)")
CAR_5_RESULT_TEXT = "60 s · 2 cones → 70 s"


def manobrabilidade_and_master(harness):
    """Event 26BR with Manobrabilidade configured (result and clock), and the
    master PC on an installer that draws both (0.7.0). Returns the master."""
    set_up_manobrabilidade(harness)
    return enroll_master(harness, FEATURES_0_7_0)


def job_waiting_for_review(harness, master, car=5, passagem=2) -> str:
    """A Manobrabilidade job of `car`'s `passagem`, in review, its table fetched once."""
    job_id = job_in_review(harness, car, passagem, worker=master)
    poll_due_tables(harness.context)
    return job_id


def click_pronto(harness, job_id, *, wait_for_result=True, **fields) -> dict:
    """ "Pronto" as the job page sends it, with the box ticked (or not)."""
    return edit_review(
        harness,
        job_id,
        action="ready",
        cut_in_s=1.0,
        cut_out_s=5.0,
        wait_for_result=wait_for_result,
        **fields,
    )


def as_time(text: str | None) -> datetime | None:
    """An API time ("2026-10-25T12:15:00Z") as a datetime, to compare with the clock."""
    return None if text is None else datetime.fromisoformat(text)


def event_kinds(harness, job_id) -> list[str]:
    """The kinds in the job's event log, oldest first (what the job page lists)."""
    response = harness.client.get(f"/jobs/{job_id}/events", auth=harness.reviewer_auth)
    response.raise_for_status()
    return [event["kind"] for event in response.json()["events"]]


def events_of_kind(harness, job_id, kind) -> list[dict]:
    response = harness.client.get(f"/jobs/{job_id}/events", auth=harness.reviewer_auth)
    response.raise_for_status()
    return [event for event in response.json()["events"] if event["kind"] == kind]


def click_render_now(harness, job_id):
    """ "Renderizar agora" on the job page (POST /jobs/{id}/render-now)."""
    return harness.client.post(f"/jobs/{job_id}/render-now", auth=harness.reviewer_auth)
