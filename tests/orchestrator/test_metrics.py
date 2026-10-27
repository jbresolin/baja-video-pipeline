"""Tests for GET /metrics (Prometheus text format, admin token only).

An external Prometheus scrapes this endpoint during the event (see
infra/vps/MONITORING.md). The tests read the text the way Prometheus would
and check that each number follows what happened: jobs per state, workers
online, bytes on the VPS disk, and the offer/claim/failure counters.
"""

from orchestrator.testing.scenarios import (
    SAMPLE_VIDEO_BYTES,
    ask_next,
    claim_ok,
    receive_job,
)
from shared.models import TaskKind, WorkerRole


def read_metrics(harness) -> dict[str, float]:
    """Scrape /metrics and return {'name{labels}': value}, skipping # comment lines.

    Example: the line `bvp_jobs{state="received"} 1` becomes
    {'bvp_jobs{state="received"}': 1.0}.
    """
    response = harness.client.get("/metrics", headers=harness.admin_headers)
    assert response.status_code == 200, response.text
    samples = {}
    for line in response.text.splitlines():
        if line and not line.startswith("#"):
            name, value = line.rsplit(" ", 1)
            samples[name] = float(value)
    return samples


def test_only_the_admin_token_may_read_metrics(harness):
    worker = harness.enroll(WorkerRole.WORKER, "pc")
    # A reviewer and a worker are both refused; nobody anonymous either.
    assert harness.client.get("/metrics", auth=harness.reviewer_auth).status_code == 401
    assert harness.client.get("/metrics", headers=worker.headers).status_code == 401
    assert harness.client.get("/metrics").status_code == 401
    # The admin gets Prometheus' text format (version 0.0.4), not JSON.
    response = harness.client.get("/metrics", headers=harness.admin_headers)
    assert response.status_code == 200
    assert response.headers["Content-Type"].startswith("text/plain; version=0.0.4")


def test_an_empty_pipeline_reports_every_series_as_zero(harness):
    # Nothing has happened yet. Every state, role and task kind must still
    # appear with 0, so a Grafana graph starts at zero instead of "no data".
    samples = read_metrics(harness)
    assert samples['bvp_jobs{state="received"}'] == 0
    assert samples['bvp_jobs{state="done"}'] == 0
    assert samples['bvp_workers_online{role="master"}'] == 0
    assert samples['bvp_task_offers_total{kind="download_raw"}'] == 0
    assert samples['bvp_task_failures_total{kind="process",scope="job"}'] == 0
    # The configured caps are there too (harness defaults: 22 GB and 25 GB).
    assert samples["bvp_inbox_hold_bytes"] == 22_000_000_000
    assert samples["bvp_inbox_cap_bytes"] == 25_000_000_000


def test_metrics_follow_a_job_through_offer_claim_and_failure(harness):
    # Set up: a master and a volunteer worker are online, one video arrives.
    master = harness.enroll(WorkerRole.MASTER, "master")
    harness.enroll(WorkerRole.WORKER, "volunteer")
    job_id = receive_job(harness)

    # The master asks for work (one offer), claims it (one claim), then
    # reports that its disk is full (one failure, scope "worker").
    offered = ask_next(harness, master, [TaskKind.DOWNLOAD_RAW])
    assert offered is not None and offered["job_id"] == job_id
    lease_id, _ = claim_ok(harness, master, job_id, TaskKind.DOWNLOAD_RAW)
    failed = harness.client.post(
        f"/jobs/{job_id}/failed",
        json={"lease_id": lease_id, "message": "disk full", "scope": "worker"},
        headers=master.headers,
    )
    assert failed.status_code == 200, failed.text

    samples = read_metrics(harness)
    # One job, still in "received" (a worker-scope failure does not fail it).
    assert samples['bvp_jobs{state="received"}'] == 1
    # Both workers heartbeated just now, so both count as online.
    assert samples['bvp_workers_online{role="master"}'] == 1
    assert samples['bvp_workers_online{role="worker"}'] == 1
    assert samples['bvp_workers_enrolled{role="studio"}'] == 0
    # Each counter moved by exactly one, under the right labels.
    assert samples['bvp_task_offers_total{kind="download_raw"}'] == 1
    assert samples['bvp_task_claims_total{kind="download_raw"}'] == 1
    assert samples['bvp_task_failures_total{kind="download_raw",scope="worker"}'] == 1
    assert samples['bvp_task_failures_total{kind="download_raw",scope="job"}'] == 0
    # The raw is still in the VPS inbox: its bytes show up there.
    assert samples['bvp_storage_bytes{area="inbox"}'] == len(SAMPLE_VIDEO_BYTES)
    assert samples['bvp_storage_bytes{area="outbox"}'] == 0


def test_a_worker_that_stops_heartbeating_drops_out_of_online(harness):
    harness.enroll(WorkerRole.MASTER, "master")
    # 46 s without a heartbeat: past the 45 s lease TTL, so "offline".
    harness.clock.advance(seconds=46)
    samples = read_metrics(harness)
    assert samples['bvp_workers_online{role="master"}'] == 0
    # Still enrolled, though: going offline does not unregister a worker.
    assert samples['bvp_workers_enrolled{role="master"}'] == 1
