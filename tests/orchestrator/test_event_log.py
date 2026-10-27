"""Tests for GET /jobs/{id}/events and GET /events (design §17).

The orchestrator already writes every state change, lease and holder
change to its events table; these endpoints let the review UI show that
log. Rules under test: oldest first, reviewer or admin only, the worker
filter, `limit` and `since`.
"""

from datetime import datetime, timedelta

from orchestrator.testing.scenarios import hold_raw, make_proxy, receive_job
from shared.models import WorkerRole


def get_events(harness, path, **params):
    response = harness.client.get(path, params=params, auth=harness.reviewer_auth)
    assert response.status_code == 200, response.text
    return response.json()["events"]


def test_a_jobs_log_lists_what_happened_oldest_first(harness):
    # A job arrives, the master downloads it and makes the proxy.
    master = harness.enroll(WorkerRole.MASTER, "master")
    job_id = receive_job(harness)
    hold_raw(harness, master, job_id)
    make_proxy(harness, master, job_id)

    events = get_events(harness, f"/jobs/{job_id}/events")
    # Every event is about this job, and ids only go up: oldest first.
    assert {event["job_id"] for event in events} == {job_id}
    assert [event["id"] for event in events] == sorted(event["id"] for event in events)
    # The state changes appear in the order they happened.
    moves = [(e["from_state"], e["to_state"]) for e in events if e["kind"] == "state_changed"]
    assert moves == [("received", "proxy_ready"), ("proxy_ready", "awaiting_review")]
    # Holder and lease events say which worker did it.
    confirmed = next(e for e in events if e["kind"] == "holder_confirmed")
    assert confirmed["worker_id"] == master.worker_id
    assert confirmed["actor"] == f"worker:{master.worker_id}"


def test_events_of_an_unknown_job_are_404(harness):
    response = harness.client.get(f"/jobs/{'0' * 32}/events", auth=harness.reviewer_auth)
    assert response.status_code == 404


def test_the_log_is_for_reviewers_and_the_admin_not_for_workers(harness):
    worker = harness.enroll(WorkerRole.WORKER, "pc")
    # The admin token works...
    assert harness.client.get("/events", headers=harness.admin_headers).status_code == 200
    # ...a worker's bearer token does not, and neither does nothing at all.
    assert harness.client.get("/events", headers=worker.headers).status_code == 401
    assert harness.client.get("/events").status_code == 401


def test_the_worker_filter_includes_events_the_worker_caused(harness):
    # Two workers enroll; each enrollment writes a "worker_enrolled" event
    # about that worker. The master also downloads a raw.
    master = harness.enroll(WorkerRole.MASTER, "master")
    other = harness.enroll(WorkerRole.WORKER, "other")
    job_id = receive_job(harness)
    hold_raw(harness, master, job_id)

    events = get_events(harness, "/events", worker_id=master.worker_id)
    # Only the master's events: either about it, or done by it.
    for event in events:
        assert master.worker_id in (event["worker_id"], event["actor"].removeprefix("worker:"))
    kinds = {event["kind"] for event in events}
    assert {"worker_enrolled", "lease_acquired", "holder_confirmed"} <= kinds
    assert other.worker_id not in {event["worker_id"] for event in events}


def test_without_since_the_newest_events_come_back_oldest_first(harness):
    # Five workers enroll: at least five events (plus token creations).
    for number in range(5):
        harness.enroll(WorkerRole.WORKER, f"pc-{number}")
    everything = get_events(harness, "/events", limit=1000)
    newest_three = get_events(harness, "/events", limit=3)
    # "limit=3" without "since" means "the 3 most recent", still in time order.
    assert [event["id"] for event in newest_three] == [e["id"] for e in everything[-3:]]


def test_since_pages_forward_from_a_point_in_time(harness):
    harness.enroll(WorkerRole.WORKER, "early")
    cut_off = harness.clock.now()
    harness.clock.advance(5)
    late = harness.enroll(WorkerRole.WORKER, "late")
    # Only events strictly after the cut-off: the late worker's enrollment.
    events = get_events(harness, "/events", since=cut_off.isoformat())
    assert events, "expected the late enrollment"
    assert all(datetime.fromisoformat(event["at"]) > cut_off for event in events)
    assert late.worker_id in {event["worker_id"] for event in events}
    # A "since" in the future gives nothing.
    future = (harness.clock.now() + timedelta(hours=1)).isoformat()
    assert get_events(harness, "/events", since=future) == []
