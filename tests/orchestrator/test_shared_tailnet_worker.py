"""A worker on a shared tailnet, "rede compartilhada" (LIVESTACK-41 item 4).

João's desktop stays on his personal tailnet, into which the VPS is
shared: it reaches the orchestrator and Caddy (same 100.x address), but
other workers and the studio cannot reach it, and it cannot reach them.
The volunteer chose "Usar a rede atual" at "Concluir conexão", and every
heartbeat now says `shared_tailnet: true`. The orchestrator must:
  * never pick it as the second holder of a raw (peers could never fetch
    from it);
  * never list it as a download source for others, and give it only VPS
    URLs (it cannot reach peers);
  * still offer it the direct push to the studio: the studio may be shared
    into its tailnet too (João's desktop delivers that way), and when it is
    not, the worker's 3 s /health check sends the clip to the outbox
    (item 13);
  * mark it on the fleet API, so the fleet page shows "rede compartilhada".
"""

from orchestrator.testing.scenarios import (
    ask_next,
    claim_ok,
    hold_raw,
    make_proxy,
    receive_job,
    review_to_ready,
)
from shared.models import TaskKind, WorkerRole


def test_a_shared_tailnet_volunteer_is_never_the_second_holder(harness):
    harness.enroll(WorkerRole.MASTER, "master")
    shared = harness.enroll(WorkerRole.WORKER, "joao-desktop")
    harness.heartbeat(shared, shared_tailnet=True)
    job_id = receive_job(harness)
    harness.clock.advance(31)  # past the fastest-downloader window

    # Nothing to download for it, ever...
    assert ask_next(harness, shared, [TaskKind.DOWNLOAD_RAW]) is None
    # ...while a normal volunteer gets the slot.
    normal = harness.enroll(WorkerRole.WORKER, "volunteer")
    assert ask_next(harness, normal, [TaskKind.DOWNLOAD_RAW])["job_id"] == job_id


def test_a_fast_shared_worker_does_not_hold_the_second_slot_hostage(harness):
    # Within 30 s of intake the slot is reserved for the fastest idle
    # downloader. A shared-tailnet worker can never take it, so its speed
    # must not make the others wait.
    harness.enroll(WorkerRole.MASTER, "master")
    slow = harness.enroll(WorkerRole.WORKER, "slow")
    fast = harness.enroll(WorkerRole.WORKER, "fast-but-shared")
    harness.heartbeat(
        fast,
        measurements=[{"kind": "download", "byte_count": 375_000_000, "seconds": 10}],
        shared_tailnet=True,
    )
    job_id = receive_job(harness)
    assert ask_next(harness, slow, [TaskKind.DOWNLOAD_RAW])["job_id"] == job_id


def test_a_shared_tailnet_master_gets_only_vps_sources_and_is_no_source_for_peers(harness):
    # The master is always a holder, shared or not (the brief only warns
    # against a shared master during an event). But nobody may be sent to
    # its :8710, and it may only be sent to the VPS.
    master = harness.enroll(WorkerRole.MASTER, "master", tailnet_address="100.80.0.1")
    harness.heartbeat(master, tailnet_address="100.80.0.1", shared_tailnet=True)
    job_id = receive_job(harness)

    offer = ask_next(harness, master, [TaskKind.DOWNLOAD_RAW])
    assert offer["download_url"].endswith(f"/inbox/{job_id}.mp4")
    assert offer["fallback_download_urls"] == []

    hold_raw(harness, master, job_id)
    harness.heartbeat(master, held_job_ids=[job_id], shared_tailnet=True)
    volunteer = harness.enroll(WorkerRole.WORKER, "volunteer")
    peer_offer = ask_next(harness, volunteer, [TaskKind.DOWNLOAD_RAW])
    assert all("100.80.0.1" not in url for url in peer_offer["fallback_download_urls"])


def test_a_shared_tailnet_worker_is_still_offered_the_direct_push(harness):
    # Arrange: a studio is online, and the master is on a shared tailnet (its
    # own Tailscale network, into which the VPS and, here, the studio are
    # shared). The reviewer marks a job ready and the master claims the render.
    master = harness.enroll(WorkerRole.MASTER, "master")
    studio = harness.enroll(WorkerRole.STUDIO, "studio", tailnet_address="100.70.0.5")
    harness.heartbeat(studio, tailnet_address="100.70.0.5")
    harness.heartbeat(master, shared_tailnet=True)
    job_id = receive_job(harness)
    hold_raw(harness, master, job_id)
    harness.heartbeat(master, held_job_ids=[job_id], shared_tailnet=True)
    make_proxy(harness, master, job_id)
    review_to_ready(harness, job_id)

    # Act: the master claims the render.
    _, task = claim_ok(harness, master, job_id, TaskKind.PROCESS)

    # Assert: it is told to push to the studio, like any worker. (Until
    # 2026-11-06 it was always sent to the outbox, which stopped João's
    # desktop from pushing to the studio shared into his tailnet.)
    assert task["delivery"]["mode"] == "direct", (
        "the orchestrator cannot know whether the studio is shared in; the worker checks"
    )
    assert task["delivery"]["studio_url"].startswith("http://100.70.0.5:8720/"), (
        "the push goes to the studio's tailnet address"
    )


def test_the_fleet_api_marks_rede_compartilhada(harness):
    shared = harness.enroll(WorkerRole.MASTER, "joao-desktop")
    harness.enroll(WorkerRole.WORKER, "volunteer")
    harness.heartbeat(shared, shared_tailnet=True)

    fleet = harness.client.get("/fleet", auth=harness.reviewer_auth).json()

    by_name = {entry["worker"]["name"]: entry["worker"] for entry in fleet["workers"]}
    assert by_name["joao-desktop"]["shared_tailnet"] is True
    assert by_name["volunteer"]["shared_tailnet"] is False


def test_switching_back_to_the_baja_tailnet_clears_the_mark(harness):
    # After "Entrar na rede Baja" the worker's heartbeats say false again.
    worker = harness.enroll(WorkerRole.WORKER, "pc")
    harness.heartbeat(worker, shared_tailnet=True)
    harness.heartbeat(worker, shared_tailnet=False)
    fleet = harness.client.get("/fleet", auth=harness.reviewer_auth).json()
    assert fleet["workers"][0]["worker"]["shared_tailnet"] is False
