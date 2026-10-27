"""Tests for /workers/{id}/next and /jobs/{id}/claim: who gets which task (design §4, §5).

Rules under test:
  * the master is always offered the download of every raw;
  * the second holder is the fastest idle volunteer (for 30 s, then anyone);
  * with the master offline, two volunteers hold the raw;
  * the studio never downloads raws; a worker near its cap neither;
  * make_proxy and process only go to confirmed holders;
  * at most one task per kind per worker;
  * an offer is not a reservation: the first claim wins.
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


def give_speed(harness, worker, mbps):
    # One measurement of 10 s at `mbps` megabits per second.
    byte_count = int(mbps * 1_000_000 / 8 * 10)
    harness.heartbeat(
        worker, measurements=[{"kind": "download", "byte_count": byte_count, "seconds": 10}]
    )


def test_master_is_always_offered_the_download(harness):
    master = harness.enroll(WorkerRole.MASTER, "master")
    job_id = receive_job(harness)
    offer = ask_next(harness, master, [TaskKind.DOWNLOAD_RAW])
    assert offer["kind"] == "download_raw"
    assert offer["job_id"] == job_id
    # The URL points at Caddy's tailnet listener, never at the orchestrator.
    assert offer["download_url"] == f"http://100.64.0.1:8080/inbox/{job_id}.mp4"


def test_second_holder_slot_prefers_the_fastest_idle_worker(harness):
    harness.enroll(WorkerRole.MASTER, "master")
    slow = harness.enroll(WorkerRole.WORKER, "slow")
    fast = harness.enroll(WorkerRole.WORKER, "fast")
    give_speed(harness, slow, 10)
    give_speed(harness, fast, 300)
    job_id = receive_job(harness)
    # The slow worker asks first but is told nothing: the fast one is preferred.
    assert ask_next(harness, slow, [TaskKind.DOWNLOAD_RAW]) is None
    # Claiming directly does not get around the rule either.
    assert claim(harness, slow, job_id, TaskKind.DOWNLOAD_RAW).status_code == 409
    assert ask_next(harness, fast, [TaskKind.DOWNLOAD_RAW])["job_id"] == job_id


def test_after_the_preference_window_any_worker_may_take_the_slot(harness):
    harness.enroll(WorkerRole.MASTER, "master")
    slow = harness.enroll(WorkerRole.WORKER, "slow")
    fast = harness.enroll(WorkerRole.WORKER, "fast")
    give_speed(harness, fast, 300)
    job_id = receive_job(harness)
    # 31 seconds later the fast worker still has not taken it (it may be
    # busy or gone), so the slow one gets it rather than nobody. Heartbeats
    # keep both "online" (a heartbeat within the last 45 s).
    harness.clock.advance(31)
    harness.heartbeat(slow)
    assert ask_next(harness, slow, [TaskKind.DOWNLOAD_RAW])["job_id"] == job_id


def test_master_plus_one_is_enough_while_the_master_is_online(harness):
    master = harness.enroll(WorkerRole.MASTER, "master")
    first = harness.enroll(WorkerRole.WORKER, "first")
    second = harness.enroll(WorkerRole.WORKER, "second")
    job_id = receive_job(harness)
    hold_raw(harness, first, job_id)
    # With master online, the one non-master slot is taken: nothing for "second".
    assert ask_next(harness, second, [TaskKind.DOWNLOAD_RAW]) is None
    # The master still gets its own copy.
    assert ask_next(harness, master, [TaskKind.DOWNLOAD_RAW])["job_id"] == job_id


def test_with_the_master_offline_two_volunteers_hold_the_raw(harness):
    master = harness.enroll(WorkerRole.MASTER, "master")
    first = harness.enroll(WorkerRole.WORKER, "first", tailnet_address="100.70.0.1")
    second = harness.enroll(WorkerRole.WORKER, "second")
    # The master's last heartbeat becomes older than 45 s: it is offline.
    harness.clock.advance(50)
    harness.heartbeat(first, tailnet_address="100.70.0.1")
    harness.heartbeat(second)
    job_id = receive_job(harness)
    hold_raw(harness, first, job_id)
    harness.clock.advance(31)  # past the fastest-downloader window
    harness.heartbeat(first, held_job_ids=[job_id])
    harness.heartbeat(second)
    offer = ask_next(harness, second, [TaskKind.DOWNLOAD_RAW])
    assert offer["job_id"] == job_id
    # A peer that holds the raw is listed as a fallback source (port 8710).
    assert offer["fallback_download_urls"] == [f"http://100.70.0.1:8710/raw/{job_id}"]
    hold_raw(harness, second, job_id)
    # When the master returns, it backfills from a peer: the VPS copy is
    # gone (two holders confirmed), so the offer's main URL is a peer.
    harness.heartbeat(first, held_job_ids=[job_id], tailnet_address="100.70.0.1")
    harness.heartbeat(master)
    backfill = ask_next(harness, master, [TaskKind.DOWNLOAD_RAW])
    assert backfill["download_url"] == f"http://100.70.0.1:8710/raw/{job_id}"


def test_studio_and_full_workers_get_no_raws(harness):
    studio = harness.enroll(WorkerRole.STUDIO, "studio")
    full = harness.enroll(WorkerRole.WORKER, "full")
    receive_job(harness)
    assert ask_next(harness, studio, [TaskKind.DOWNLOAD_RAW]) is None
    # 1 GB free under its cap: near the cap, so no new raws (design §9)...
    answer = harness.heartbeat(full, free_bytes=1_000_000_000)
    assert answer["accepting_new_raws"] is False
    assert ask_next(harness, full, [TaskKind.DOWNLOAD_RAW]) is None


def test_proxy_and_process_go_only_to_confirmed_holders(harness):
    master = harness.enroll(WorkerRole.MASTER, "master")
    outsider = harness.enroll(WorkerRole.WORKER, "outsider")
    job_id = receive_job(harness)
    # Before anyone holds the raw, nobody can make the proxy.
    assert ask_next(harness, master, [TaskKind.MAKE_PROXY]) is None
    hold_raw(harness, master, job_id)
    assert ask_next(harness, outsider, [TaskKind.MAKE_PROXY]) is None
    assert ask_next(harness, master, [TaskKind.MAKE_PROXY])["job_id"] == job_id
    make_proxy(harness, master, job_id)
    review_to_ready(harness, job_id)
    # Processing (and reprocessing) is holders-only too.
    assert ask_next(harness, outsider, [TaskKind.PROCESS]) is None
    response = claim(harness, outsider, job_id, TaskKind.PROCESS)
    assert response.json()["error"]["code"] == "not_claimable"
    assert ask_next(harness, master, [TaskKind.PROCESS])["job_id"] == job_id


def test_one_task_per_kind_per_worker(harness):
    master = harness.enroll(WorkerRole.MASTER, "master")
    first_job = receive_job(harness, b"first video" * 100)
    receive_job(harness, b"second video" * 100)
    claim_ok(harness, master, first_job, TaskKind.DOWNLOAD_RAW)
    # Busy downloading: no second download offer even though one is waiting.
    assert ask_next(harness, master, [TaskKind.DOWNLOAD_RAW]) is None


def test_the_first_claim_wins_and_the_second_gets_already_claimed(harness):
    master = harness.enroll(WorkerRole.MASTER, "master")
    other = harness.enroll(WorkerRole.WORKER, "other")
    job_id = receive_job(harness)
    hold_raw(harness, master, job_id)
    hold_raw(harness, other, job_id)
    # Both holders get the same make_proxy offer...
    assert ask_next(harness, master, [TaskKind.MAKE_PROXY])["job_id"] == job_id
    assert ask_next(harness, other, [TaskKind.MAKE_PROXY])["job_id"] == job_id
    # ...the first claim wins, the second is told someone else has it.
    claim_ok(harness, master, job_id, TaskKind.MAKE_PROXY)
    lost = claim(harness, other, job_id, TaskKind.MAKE_PROXY)
    assert lost.status_code == 409
    assert lost.json()["error"]["code"] == "already_claimed"


def test_claiming_twice_returns_the_same_lease(harness):
    master = harness.enroll(WorkerRole.MASTER, "master")
    job_id = receive_job(harness)
    first_lease, _ = claim_ok(harness, master, job_id, TaskKind.DOWNLOAD_RAW)
    # The worker never saw the first answer and claims again: same lease.
    second_lease, _ = claim_ok(harness, master, job_id, TaskKind.DOWNLOAD_RAW)
    assert first_lease == second_lease


def test_claiming_an_unknown_job_is_404(harness):
    master = harness.enroll(WorkerRole.MASTER, "master")
    response = claim(harness, master, "0" * 32, TaskKind.DOWNLOAD_RAW)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
