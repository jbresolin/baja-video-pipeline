"""Tests for POST /jobs/{id}/failed and its `scope` (design §17).

The rules under test:
  * scope "job" (the input is bad) fails the job at once;
  * scope "worker" (this worker could not do it: disk, local ffmpeg,
    network) only excludes that worker from that task on that job, and the
    task is offered to someone else;
  * the job fails once two *different* workers reported scope "worker" for
    the *same* task;
  * leaving scope out means "worker".
"""

from orchestrator.testing.scenarios import (
    ask_next,
    claim,
    claim_ok,
    hold_raw,
    make_proxy,
    receive_job,
    review_to_ready,
)
from shared.models import TaskKind, WorkerRole


def report_failure(harness, worker, job_id, lease_id, *, scope=None, message="it broke"):
    """POST /failed as `worker`. scope=None leaves the field out (the default)."""
    body = {"lease_id": lease_id, "message": message}
    if scope is not None:
        body["scope"] = scope
    response = harness.client.post(f"/jobs/{job_id}/failed", json=body, headers=worker.headers)
    assert response.status_code == 200, response.text
    return response.json()["state"]


def fail_download(harness, worker, job_id, scope=None) -> str:
    """Claim download_raw as `worker` and report it failed; returns the job's new state."""
    lease_id, _ = claim_ok(harness, worker, job_id, TaskKind.DOWNLOAD_RAW)
    return report_failure(harness, worker, job_id, lease_id, scope=scope)


def test_scope_job_fails_the_job_at_once(harness):
    # One master downloads a raw whose inbox copy is bad (the hash does not
    # match at the source) and says so with scope "job".
    master = harness.enroll(WorkerRole.MASTER, "master")
    job_id = receive_job(harness)
    assert fail_download(harness, master, job_id, scope="job") == "failed"
    # A single report was enough, and the job remembers where it failed from.
    job = harness.job(job_id)
    assert job["state"] == "failed"
    assert job["failed_from"] == "received"


def test_scope_defaults_to_worker_and_one_report_does_not_fail_the_job(harness):
    # The master's disk is full, so its download fails. It sends no scope,
    # which means "worker": the job must carry on without it.
    master = harness.enroll(WorkerRole.MASTER, "master")
    volunteer = harness.enroll(WorkerRole.WORKER, "volunteer")
    job_id = receive_job(harness)
    assert fail_download(harness, master, job_id) == "received"
    job = harness.job(job_id)
    assert job["failure"]["message"] == "it broke"
    assert "it broke" in job["waiting_reason"]
    # The master is not offered that download again...
    assert claim(harness, master, job_id, TaskKind.DOWNLOAD_RAW).status_code == 409
    # ...but another worker is, so the task is effectively re-offered.
    assert ask_next(harness, volunteer, [TaskKind.DOWNLOAD_RAW])["job_id"] == job_id


def test_two_different_workers_with_worker_scope_fail_the_job(harness):
    # Three machines: the master and two volunteers. The master online
    # means only one volunteer slot exists, so the second volunteer only
    # gets its chance after the first one failed and was excluded.
    master = harness.enroll(WorkerRole.MASTER, "master")
    first = harness.enroll(WorkerRole.WORKER, "first")
    second = harness.enroll(WorkerRole.WORKER, "second")
    job_id = receive_job(harness)
    harness.clock.advance(31)  # past the fastest-downloader preference window
    for worker in (master, first, second):
        harness.heartbeat(worker)

    # Failure number one (scope worker, from the master): the job carries on.
    assert fail_download(harness, master, job_id, scope="worker") == "received"
    # Failure number two, from a *different* worker, same task: the job fails.
    assert fail_download(harness, first, job_id, scope="worker") == "failed"
    job = harness.job(job_id)
    assert job["failed_from"] == "received"
    assert job["failure"]["worker_id"] == first.worker_id
    # Failing the job revoked every lease, so the job is quiet until a
    # reviewer chooses retry or discard.
    assert job["leases"] == []
    assert ask_next(harness, second, [TaskKind.DOWNLOAD_RAW]) is None


def test_worker_scope_failures_of_different_tasks_do_not_add_up(harness):
    # One worker fails the download, another fails the proxy. Each is a
    # single "worker"-scope report for its own task, so neither task has
    # reached two workers and the job must not fail.
    master = harness.enroll(WorkerRole.MASTER, "master")
    volunteer = harness.enroll(WorkerRole.WORKER, "volunteer")
    job_id = receive_job(harness)
    hold_raw(harness, master, job_id)
    assert fail_download(harness, volunteer, job_id, scope="worker") == "received"
    lease_id, _ = claim_ok(harness, master, job_id, TaskKind.MAKE_PROXY)
    assert report_failure(harness, master, job_id, lease_id, scope="worker") == "received"


def test_a_process_failure_with_worker_scope_hands_the_render_to_the_other_holder(harness):
    # Both the master and a volunteer hold the raw; the job is reviewed.
    master = harness.enroll(WorkerRole.MASTER, "master")
    volunteer = harness.enroll(WorkerRole.WORKER, "volunteer")
    job_id = receive_job(harness)
    hold_raw(harness, master, job_id)
    hold_raw(harness, volunteer, job_id)
    make_proxy(harness, master, job_id)
    review_to_ready(harness, job_id)

    # The master's local ffmpeg crashes while rendering (scope worker).
    lease_id, _ = claim_ok(harness, master, job_id, TaskKind.PROCESS)
    assert report_failure(harness, master, job_id, lease_id, scope="worker") == "ready"
    # "claimed" went back to "ready", so the other holder is offered the render
    # and the master is not.
    assert ask_next(harness, master, [TaskKind.PROCESS]) is None
    assert ask_next(harness, volunteer, [TaskKind.PROCESS])["job_id"] == job_id

    # The volunteer crashes too: two different workers, same task → failed,
    # and retry would go back to "ready" because it failed from "claimed".
    lease_id, _ = claim_ok(harness, volunteer, job_id, TaskKind.PROCESS)
    assert report_failure(harness, volunteer, job_id, lease_id, scope="worker") == "failed"
    assert harness.job(job_id)["failed_from"] == "claimed"


def test_a_retry_forgets_earlier_worker_scope_failures(harness):
    # Two worker-scope failures failed the job; a reviewer clicks retry.
    master = harness.enroll(WorkerRole.MASTER, "master")
    first = harness.enroll(WorkerRole.WORKER, "first")
    job_id = receive_job(harness)
    harness.clock.advance(31)
    harness.heartbeat(master)
    harness.heartbeat(first)
    fail_download(harness, master, job_id, scope="worker")
    assert fail_download(harness, first, job_id, scope="worker") == "failed"
    job = harness.job(job_id)
    retried = harness.client.patch(
        f"/jobs/{job_id}/review",
        json={"expected_version": job["version"], "action": "retry"},
        auth=harness.reviewer_auth,
    )
    assert retried.json()["state"] == "received"
    # After the retry the slate is clean: the master may download again,
    # and its next worker-scope failure alone does not fail the job.
    assert fail_download(harness, master, job_id, scope="worker") == "received"
