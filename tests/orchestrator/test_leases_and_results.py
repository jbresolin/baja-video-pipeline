"""Tests for leases (heartbeat renewal, expiry, /lease/renew) and task results
(holder-confirm, failure reports), docs/api.md §5 and design §4.
"""

from orchestrator.background.sweepers import expire_leases_once
from orchestrator.testing.scenarios import (
    claim_ok,
    hold_raw,
    make_proxy,
    receive_job,
    review_to_ready,
)
from shared.models import TaskKind, WorkerRole


def test_a_heartbeat_renews_listed_leases_and_reports_unknown_ones_lost(harness):
    master = harness.enroll(WorkerRole.MASTER, "master")
    job_id = receive_job(harness)
    lease_id, _ = claim_ok(harness, master, job_id, TaskKind.DOWNLOAD_RAW)
    # 40 s later (lease TTL is 45 s) the heartbeat renews it...
    harness.clock.advance(40)
    answer = harness.heartbeat(master, current_lease_ids=[lease_id, "no-such-lease"])
    assert answer["lease_status"] == {lease_id: "renewed", "no-such-lease": "lost"}
    # ...so 40 s after that it is still alive (80 s after the claim).
    harness.clock.advance(40)
    answer = harness.heartbeat(master, current_lease_ids=[lease_id])
    assert answer["lease_status"] == {lease_id: "renewed"}


def test_an_expired_process_lease_sends_the_job_back_to_ready(harness):
    master = harness.enroll(WorkerRole.MASTER, "master")
    job_id = receive_job(harness)
    hold_raw(harness, master, job_id)
    make_proxy(harness, master, job_id)
    review_to_ready(harness, job_id)
    lease_id, _ = claim_ok(harness, master, job_id, TaskKind.PROCESS)
    assert harness.job(job_id)["state"] == "claimed"
    # The worker crashes: no heartbeat for 46 s. One pass of the sweeper
    # (with the fake clock, no waiting) ends the lease...
    harness.clock.advance(46)
    assert expire_leases_once(harness.context) == [lease_id]
    # ...and the job is back in the queue for another holder.
    assert harness.job(job_id)["state"] == "ready"
    # The worker comes back and learns its lease is gone.
    answer = harness.heartbeat(master, current_lease_ids=[lease_id], held_job_ids=[job_id])
    assert answer["lease_status"] == {lease_id: "lost"}


def test_renew_endpoint_answers_lease_lost_for_a_dead_lease(harness):
    master = harness.enroll(WorkerRole.MASTER, "master")
    job_id = receive_job(harness)
    lease_id, _ = claim_ok(harness, master, job_id, TaskKind.DOWNLOAD_RAW)
    renewed = harness.client.post(
        f"/jobs/{job_id}/lease/renew", json={"lease_id": lease_id}, headers=master.headers
    )
    assert renewed.status_code == 200
    harness.clock.advance(46)
    late = harness.client.post(
        f"/jobs/{job_id}/lease/renew", json={"lease_id": lease_id}, headers=master.headers
    )
    assert late.status_code == 409
    assert late.json()["error"]["code"] == "lease_lost"


def test_holder_confirm_with_the_wrong_hash_is_422_and_drops_the_holder(harness):
    master = harness.enroll(WorkerRole.MASTER, "master")
    job_id = receive_job(harness)
    lease_id, task = claim_ok(harness, master, job_id, TaskKind.DOWNLOAD_RAW)
    response = harness.client.post(
        f"/jobs/{job_id}/holder-confirm",
        json={"lease_id": lease_id, "sha256": "f" * 64, "size_bytes": task["size_bytes"]},
        headers=master.headers,
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "hash_mismatch"
    # The worker is no holder and its lease ended; it may try again from scratch.
    assert harness.job(job_id)["holders"] == []
    assert harness.job(job_id)["leases"] == []


def test_the_first_confirming_holder_fills_in_duration_and_audio(harness):
    master = harness.enroll(WorkerRole.MASTER, "master")
    job_id = receive_job(harness)
    hold_raw(harness, master, job_id)  # reports duration 12 s, has_audio true
    job = harness.job(job_id)
    assert (job["duration_s"], job["has_audio"]) == (12.0, True)
    assert job["holders"][0]["status"] == "confirmed"
    # One holder is not enough: the VPS keeps its copy.
    assert job["raw_on_vps"] is True


def test_heartbeat_forgets_a_holder_whose_raw_disappeared(harness):
    master = harness.enroll(WorkerRole.MASTER, "master")
    job_id = receive_job(harness)
    hold_raw(harness, master, job_id)
    # The worker's held_job_ids no longer list the job (disk wiped, say).
    harness.heartbeat(master, held_job_ids=[])
    assert harness.job(job_id)["holders"] == []


def report_failure(harness, worker, job_id, lease_id, message="ffmpeg exited with 1", scope="job"):
    """POST /failed. scope "job" (bad input) unless a test says "worker"."""
    return harness.client.post(
        f"/jobs/{job_id}/failed",
        json={"lease_id": lease_id, "message": message, "exit_code": 1, "scope": scope},
        headers=worker.headers,
    )


def test_a_download_failure_while_received_fails_the_job_and_records_where(harness):
    master = harness.enroll(WorkerRole.MASTER, "master")
    job_id = receive_job(harness)
    lease_id, _ = claim_ok(harness, master, job_id, TaskKind.DOWNLOAD_RAW)
    response = report_failure(harness, master, job_id, lease_id, "ffprobe: invalid data")
    assert response.json() == {"job_id": job_id, "state": "failed"}
    job = harness.job(job_id)
    # Design §4 as amended: failed from received, remembered in failed_from.
    assert job["failed_from"] == "received"
    assert job["failure"]["message"] == "ffprobe: invalid data"
    assert job["failure"]["task_kind"] == "download_raw"


def test_a_process_failure_fails_the_job_from_claimed(harness):
    master = harness.enroll(WorkerRole.MASTER, "master")
    job_id = receive_job(harness)
    hold_raw(harness, master, job_id)
    make_proxy(harness, master, job_id)
    review_to_ready(harness, job_id)
    lease_id, _ = claim_ok(harness, master, job_id, TaskKind.PROCESS)
    assert report_failure(harness, master, job_id, lease_id).json()["state"] == "failed"
    assert harness.job(job_id)["failed_from"] == "claimed"
    # The failed job shows in the review queue with the worker's message.
    queue = harness.client.get("/review/queue?state=failed", auth=harness.reviewer_auth).json()
    assert queue["items"][0]["failure_message"] == "ffmpeg exited with 1"


def test_a_late_download_failure_keeps_the_state_and_excludes_only_that_worker(harness):
    master = harness.enroll(WorkerRole.MASTER, "master")
    other = harness.enroll(WorkerRole.WORKER, "other")
    job_id = receive_job(harness)
    hold_raw(harness, master, job_id)
    make_proxy(harness, master, job_id)  # job is now awaiting_review
    lease_id, _ = claim_ok(harness, other, job_id, TaskKind.DOWNLOAD_RAW)
    response = report_failure(harness, other, job_id, lease_id, "disk full", scope="worker")
    # awaiting_review cannot fail (design §4): the job carries on...
    assert response.json()["state"] == "awaiting_review"
    job = harness.job(job_id)
    assert "disk full" in job["waiting_reason"]
    # ...but that worker is not offered that download for that job again.
    again = harness.client.post(
        f"/jobs/{job_id}/claim", json={"task_kind": "download_raw"}, headers=other.headers
    )
    assert again.json()["error"]["code"] == "not_claimable"


def test_failure_report_without_a_live_lease_is_lease_lost(harness):
    master = harness.enroll(WorkerRole.MASTER, "master")
    job_id = receive_job(harness)
    response = report_failure(harness, master, job_id, "made-up")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "lease_lost"
