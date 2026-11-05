"""A paused worker (LIVESTACK-38 item 9; docs/api.md §5 "Pause").

The volunteer clicks "Pausar" in the tray icon. The worker then sends
`paused: true` in every heartbeat. The orchestrator must:
  * offer it nothing (no download, proxy, process or deliver) and refuse
    its claims;
  * not count it when choosing the second holder or the master's own copy
    (it behaves like an offline worker there), and not push clips to a
    paused studio;
  * still list it as a source of the raws it holds (it keeps serving them
    to peers on :8710);
  * show `paused` on the fleet API, so the fleet page can say "pausado".
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

ALL_WORKER_KINDS = [TaskKind.DOWNLOAD_RAW, TaskKind.MAKE_PROXY, TaskKind.PROCESS]


def test_a_paused_worker_is_offered_nothing_and_cannot_claim(harness):
    # Set-up: a master holding a reviewed job, so normally it would get the
    # "process" task straight away.
    master = harness.enroll(WorkerRole.MASTER, "master")
    job_id = receive_job(harness)
    hold_raw(harness, master, job_id)
    make_proxy(harness, master, job_id)
    review_to_ready(harness, job_id)

    # The volunteer pauses it: the next heartbeat says so. (Heartbeats list
    # the raws the worker holds; leaving them out would drop it as holder.)
    harness.heartbeat(master, held_job_ids=[job_id], paused=True)

    # /next has nothing for it, whatever kinds it says it accepts…
    assert ask_next(harness, master, ALL_WORKER_KINDS) is None
    # …and claiming directly is refused too (409 not_claimable).
    response = claim(harness, master, job_id, TaskKind.PROCESS)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "not_claimable"

    # "Retomar": the next heartbeat says paused=false and work flows again.
    harness.heartbeat(master, held_job_ids=[job_id], paused=False)
    assert ask_next(harness, master, ALL_WORKER_KINDS)["kind"] == "process"


def test_a_paused_volunteer_is_not_preferred_as_second_holder(harness):
    # Two volunteers; the paused one is much faster. Normally the fast one
    # would get the second-holder slot for the first 30 s. Paused, it must
    # not hold the slot hostage: the slow one is offered the download now.
    harness.enroll(WorkerRole.MASTER, "master")
    slow = harness.enroll(WorkerRole.WORKER, "slow")
    fast = harness.enroll(WorkerRole.WORKER, "fast")
    harness.heartbeat(
        fast,
        measurements=[{"kind": "download", "byte_count": 375_000_000, "seconds": 10}],
        paused=True,
    )
    job_id = receive_job(harness)
    assert ask_next(harness, slow, [TaskKind.DOWNLOAD_RAW])["job_id"] == job_id


def test_a_paused_master_counts_as_away_so_two_volunteers_hold_the_raw(harness):
    # With the master online, one volunteer copy is enough (master + 1).
    # A paused master will not download, so like an offline master it
    # must not count: a second volunteer is offered the raw.
    master = harness.enroll(WorkerRole.MASTER, "master")
    first = harness.enroll(WorkerRole.WORKER, "first")
    second = harness.enroll(WorkerRole.WORKER, "second")
    harness.heartbeat(master, paused=True)
    job_id = receive_job(harness)
    hold_raw(harness, first, job_id)
    harness.clock.advance(31)  # past the fastest-downloader window
    harness.heartbeat(first, held_job_ids=[job_id])
    harness.heartbeat(second)
    harness.heartbeat(master, paused=True)
    assert ask_next(harness, second, [TaskKind.DOWNLOAD_RAW])["job_id"] == job_id


def test_a_paused_holder_still_serves_its_raw_to_peers(harness):
    # The paused worker keeps its file server running, so it stays a
    # download source for others (listed in fallback_download_urls). Here
    # the master is offline, so a second volunteer copy is wanted.
    master = harness.enroll(WorkerRole.MASTER, "master")
    holder = harness.enroll(WorkerRole.WORKER, "holder", tailnet_address="100.70.0.1")
    other = harness.enroll(WorkerRole.WORKER, "other")
    harness.clock.advance(50)  # the master's last heartbeat is now too old
    job_id = receive_job(harness)
    harness.heartbeat(holder)
    hold_raw(harness, holder, job_id)
    harness.clock.advance(31)  # past the fastest-downloader window
    harness.heartbeat(holder, held_job_ids=[job_id], paused=True)
    harness.heartbeat(other)
    assert master  # (enrolled only so that "the master is offline" is true)
    offer = ask_next(harness, other, [TaskKind.DOWNLOAD_RAW])
    assert offer is not None
    assert f"http://100.70.0.1:8710/raw/{job_id}" in offer["fallback_download_urls"]


def test_a_paused_studio_gets_no_direct_pushes(harness):
    # Normally a studio that heartbeated recently gets clips pushed directly.
    # Paused, the process task must say "outbox" instead.
    master = harness.enroll(WorkerRole.MASTER, "master")
    studio = harness.enroll(WorkerRole.STUDIO, "studio", tailnet_address="100.70.0.5")
    harness.heartbeat(studio, tailnet_address="100.70.0.5", paused=True)
    job_id = receive_job(harness)
    hold_raw(harness, master, job_id)
    make_proxy(harness, master, job_id)
    review_to_ready(harness, job_id)
    _, task = claim_ok(harness, master, job_id, TaskKind.PROCESS)
    assert task["delivery"] == {"mode": "outbox", "studio_url": None}


def test_the_fleet_api_shows_who_is_paused(harness):
    paused = harness.enroll(WorkerRole.WORKER, "paused-pc")
    harness.enroll(WorkerRole.WORKER, "busy-pc")
    harness.heartbeat(paused, paused=True)
    fleet = harness.client.get("/fleet", auth=harness.reviewer_auth).json()
    by_name = {entry["worker"]["name"]: entry for entry in fleet["workers"]}
    assert by_name["paused-pc"]["paused"] is True
    assert by_name["busy-pc"]["paused"] is False
    # Paused is not offline: it still heartbeats.
    assert by_name["paused-pc"]["online"] is True
