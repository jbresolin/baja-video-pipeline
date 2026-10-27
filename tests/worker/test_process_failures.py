"""process: how a failed render or delivery is reported (design §17).

POST /jobs/{id}/failed carries a scope: "job" when the input itself is bad
(no worker could do better), "worker" when this machine could not do it
(the task is offered to another holder). See worker/failures.py.
"""

from shared.models import FailureScope
from tests.worker.fake_orchestrator import BASE_URL
from tests.worker.fakes import always_unavailable
from tests.worker.test_process import hold_raw, process_task
from worker.media_tools import MediaToolError


def test_render_failure_is_reported_as_this_workers_problem(harness, raw_bytes):
    # ffmpeg exits with an error while rendering. The raw was readable, so
    # this machine's ffmpeg is to blame: scope "worker", and another worker
    # gets a chance (design §17).
    job = hold_raw(harness, raw_bytes)
    harness.media.failing_render = "final render"

    harness.claim_and_run(process_task(job))

    [(_, failure)] = harness.orchestrator.failures
    assert failure.exit_code == 1 and "final render" in failure.message
    assert failure.scope == FailureScope.WORKER


def test_unreadable_raw_is_reported_as_the_jobs_problem(harness, raw_bytes):
    # ffprobe cannot read the raw at all: no worker can render it, so the
    # job fails at once (scope "job").
    job = hold_raw(harness, raw_bytes)
    harness.media.probe_error = MediaToolError("ffprobe cannot read the file: EOF", 1)

    harness.claim_and_run(process_task(job))

    [(_, failure)] = harness.orchestrator.failures
    assert failure.scope == FailureScope.JOB
    assert "not a readable video" in failure.message


def test_missing_raw_is_reported_as_this_workers_problem(harness, raw_bytes):
    # The orchestrator thinks we hold the raw, but it is not on disk
    # (deleted by hand). Not the video's fault: scope "worker".
    job = harness.orchestrator.add_job(raw_bytes)

    harness.claim_and_run(process_task(job))

    [(_, failure)] = harness.orchestrator.failures
    assert failure.scope == FailureScope.WORKER
    assert harness.media.commands == []


def test_outbox_unreachable_after_retries_is_reported_as_this_workers_problem(harness, raw_bytes):
    # Setup: the outbox upload endpoint answers 503 every time, but the
    # rest of the API (including POST /failed) works. After its retries
    # the worker reports the failure (scope "worker") instead of silently
    # letting the lease expire, so the task moves on to another worker.
    job = hold_raw(harness, raw_bytes)
    harness.network.override_path(BASE_URL, f"/jobs/{job.job_id}/clip", always_unavailable())

    harness.claim_and_run(process_task(job))

    [(_, failure)] = harness.orchestrator.failures
    assert failure.scope == FailureScope.WORKER
    assert "outbox upload failed" in failure.message
    # The lease ended with the report: nothing left for heartbeats to renew.
    assert harness.leases.ids() == []
