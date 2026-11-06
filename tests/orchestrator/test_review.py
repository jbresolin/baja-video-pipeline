"""Tests for the review endpoints: PATCH /jobs/{id}/review, regenerate-proxy, teams.

docs/api.md §7.1 "Review update" lists which action is allowed from which
state; each test below checks one row of that table or one validation rule.
"""

import pytest

from orchestrator.testing.scenarios import (
    claim_ok,
    hold_raw,
    make_proxy,
    receive_job,
    review_to_ready,
)
from shared.models import TaskKind, WorkerRole


@pytest.fixture
def job_in_review(harness):
    """A job waiting for review (held by the master, proxy uploaded). Returns (master, job id)."""
    master = harness.enroll(WorkerRole.MASTER, "master", tailnet_address="100.70.0.9")
    job_id = receive_job(harness)
    hold_raw(harness, master, job_id)  # duration 12 s
    make_proxy(harness, master, job_id)
    return master, job_id


def patch_review(harness, job_id, **body):
    if "expected_version" not in body:
        body["expected_version"] = harness.job(job_id)["version"]
    return harness.client.patch(f"/jobs/{job_id}/review", json=body, auth=harness.reviewer_auth)


def test_save_stores_fields_resolves_the_team_and_records_the_reviewer(harness, job_in_review):
    _, job_id = job_in_review
    harness.add_team(7, "Equipe Sete")
    response = patch_review(harness, job_id, action="save", prova="Suspensão", car_number=7)
    assert response.status_code == 200
    job = response.json()
    # "save" never changes the state.
    assert job["state"] == "awaiting_review"
    assert (job["prova"], job["car_number"], job["team_name"]) == ("Suspensão", 7, "Equipe Sete")
    assert job["reviewed_by"] == "ana"


def test_a_stale_version_is_a_conflict(harness, job_in_review):
    _, job_id = job_in_review
    stale = harness.job(job_id)["version"]
    patch_review(harness, job_id, action="save", prova="Enduro")  # someone else saves first
    response = patch_review(harness, job_id, expected_version=stale, action="save", prova="X")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "version_conflict"
    assert response.json()["error"]["details"]["current_version"] == stale + 1


def test_ready_needs_every_field_and_says_which_are_missing(harness, job_in_review):
    _, job_id = job_in_review
    response = patch_review(harness, job_id, action="ready", prova="Enduro", car_number=42)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "missing_review_fields"
    assert response.json()["error"]["details"]["missing"] == ["passagem", "cut_in_s", "cut_out_s"]


def test_a_cut_past_the_end_of_the_video_is_refused(harness, job_in_review):
    _, job_id = job_in_review
    # The holder reported a 12 s video; 15 s does not exist.
    response = patch_review(harness, job_id, action="save", cut_in_s=1, cut_out_s=15)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_actions_not_allowed_in_the_current_state_are_illegal_transitions(harness, job_in_review):
    _, job_id = job_in_review
    # "retry" is only for failed jobs, "reprocess" only for processed ones.
    for action in ("retry", "reprocess"):
        response = patch_review(harness, job_id, action=action)
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "illegal_transition"


def test_discard_moves_to_discarded_and_blocks_editing(harness, job_in_review):
    # What happens to the raws (kept for a grace period, then deleted) and
    # how "restore" undoes a discard: tests/orchestrator/test_discard_restore.py.
    _, job_id = job_in_review
    response = patch_review(harness, job_id, action="discard")
    assert response.json()["state"] == "discarded"
    assert response.json()["discarded_from"] == "awaiting_review"
    # A discarded job cannot be edited: "save" is refused (only "restore" is accepted).
    assert patch_review(harness, job_id, action="save", prova="x").status_code == 409


def test_reprocess_revokes_the_process_lease_and_queues_the_job_again(harness, job_in_review):
    master, job_id = job_in_review
    review_to_ready(harness, job_id)
    lease_id, _ = claim_ok(harness, master, job_id, TaskKind.PROCESS)
    response = patch_review(harness, job_id, action="reprocess", cut_in_s=2, cut_out_s=6)
    assert response.json()["state"] == "ready"
    # The worker learns on its next heartbeat that it must stop.
    answer = harness.heartbeat(master, current_lease_ids=[lease_id], held_job_ids=[job_id])
    assert answer["lease_status"] == {lease_id: "lost"}


def fail_job(harness, worker, job_id, kind):
    # scope "job" (bad input) fails the job straight away (design §17).
    lease_id, _ = claim_ok(harness, worker, job_id, kind)
    harness.client.post(
        f"/jobs/{job_id}/failed",
        json={"lease_id": lease_id, "message": "broken", "scope": "job"},
        headers=worker.headers,
    )


def test_retry_after_a_processing_failure_goes_back_to_ready(harness, job_in_review):
    master, job_id = job_in_review
    review_to_ready(harness, job_id)
    fail_job(harness, master, job_id, TaskKind.PROCESS)
    response = patch_review(harness, job_id, action="retry")
    job = response.json()
    # failed_from was "claimed"; retry lands on "ready", the queue state.
    assert job["state"] == "ready"
    assert job["failure"] is None and job["failed_from"] is None


def test_retry_after_an_intake_failure_goes_back_to_received(harness):
    master = harness.enroll(WorkerRole.MASTER, "master")
    job_id = receive_job(harness)
    fail_job(harness, master, job_id, TaskKind.DOWNLOAD_RAW)
    # No review fields are needed: the job is not going to "ready".
    response = patch_review(harness, job_id, action="retry")
    assert response.json()["state"] == "received"
    # The master is offered the download again.
    offer = harness.client.post(
        f"/workers/{master.worker_id}/next",
        json={"accept_kinds": ["download_raw"]},
        headers=master.headers,
    )
    assert offer.json()["task"]["job_id"] == job_id


def test_regenerate_proxy_queues_a_make_proxy_for_a_holder(harness, job_in_review):
    master, job_id = job_in_review
    response = harness.client.post(f"/jobs/{job_id}/regenerate-proxy", auth=harness.reviewer_auth)
    assert response.status_code == 202
    assert response.json() == {"job_id": job_id, "queued": True, "waiting_reason": None}
    # The state does not change, and the holder is offered the proxy again.
    assert harness.job(job_id)["state"] == "awaiting_review"
    offer = harness.client.post(
        f"/workers/{master.worker_id}/next",
        json={"accept_kinds": ["make_proxy"]},
        headers=master.headers,
    )
    assert offer.json()["task"]["kind"] == "make_proxy"


def test_regenerate_proxy_says_so_when_no_holder_is_online(harness, job_in_review):
    _, job_id = job_in_review
    harness.clock.advance(60)  # the only holder stops sending heartbeats
    response = harness.client.post(f"/jobs/{job_id}/regenerate-proxy", auth=harness.reviewer_auth)
    assert response.json()["queued"] is True
    assert "holder" in response.json()["waiting_reason"]


def test_team_lookup_endpoint(harness):
    harness.add_team(42, "Equipe X", short_name="X")
    found = harness.client.get("/teams/42", auth=harness.reviewer_auth)
    assert found.json() == {
        "car_number": 42,
        "team_name": "Equipe X",
        "school": None,
        "short_name": "X",
        "school_short": None,
        # How a clip names them under the default choices (short name; no
        # school known at all). test_clip_names.py covers the choices.
        "clip_team_name": "X",
        "clip_school_name": None,
    }
    missing = harness.client.get("/teams/41", auth=harness.reviewer_auth)
    assert missing.status_code == 404


def test_a_car_with_no_team_still_saves_with_an_empty_team_name(harness, job_in_review):
    # A team name is nice to have; it must never block a reviewer (design §18).
    _, job_id = job_in_review
    response = patch_review(harness, job_id, action="save", car_number=42)
    assert response.status_code == 200
    assert response.json()["team_name"] is None
