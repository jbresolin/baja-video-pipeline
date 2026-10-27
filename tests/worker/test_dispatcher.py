"""Dispatcher: ask /next for idle stages only, claim, hand over (docs/api.md §5)."""

from shared.models import DownloadRawTask, MakeProxyTask, TaskKind, WorkerRole
from tests.worker.conftest import GB, build_harness
from tests.worker.fake_orchestrator import BASE_URL


def download_offer(job_id="job-1", size_bytes=1000) -> DownloadRawTask:
    return DownloadRawTask(
        job_id=job_id,
        sha256="a" * 64,
        size_bytes=size_bytes,
        download_url=f"{BASE_URL}/inbox/{job_id}.mp4",
    )


def proxy_offer(job_id="job-2") -> MakeProxyTask:
    return MakeProxyTask(
        job_id=job_id,
        raw_sha256="b" * 64,
        proxy_upload_url=f"{BASE_URL}/jobs/{job_id}/proxy",
        thumbnail_upload_url=f"{BASE_URL}/jobs/{job_id}/thumbnail",
    )


def test_asks_for_every_kind_when_idle(harness):
    harness.dispatcher.run_once()
    [request] = harness.orchestrator.next_requests
    assert request.accept_kinds == [TaskKind.DOWNLOAD_RAW, TaskKind.MAKE_PROXY, TaskKind.PROCESS]


def test_claimed_task_goes_to_its_stage_and_busy_kinds_are_left_out(harness):
    # Setup: one download offer. The dispatcher claims it and hands it to
    # the download stage (which we do not run, so it stays busy).
    harness.orchestrator.offer(download_offer())
    harness.dispatcher.run_once()
    assert not harness.stages[TaskKind.DOWNLOAD_RAW].is_idle
    # The lease is now renewed by our heartbeats.
    assert len(harness.leases.ids()) == 1

    # Next round: at most one task per stage, so download_raw is not asked for.
    harness.dispatcher.run_once()
    assert harness.orchestrator.next_requests[-1].accept_kinds == [
        TaskKind.MAKE_PROXY,
        TaskKind.PROCESS,
    ]


def test_stages_run_concurrently_one_task_each(harness):
    # A download and a proxy can both be claimed: different stages.
    harness.orchestrator.offer(download_offer())
    harness.orchestrator.offer(proxy_offer())
    harness.dispatcher.run_once()
    harness.dispatcher.run_once()
    assert not harness.stages[TaskKind.DOWNLOAD_RAW].is_idle
    assert not harness.stages[TaskKind.MAKE_PROXY].is_idle
    assert harness.stages[TaskKind.PROCESS].is_idle


def test_lost_claim_race_just_asks_again(harness):
    # Another worker claimed the same offer first: 409 already_claimed.
    harness.orchestrator.offer(download_offer())
    harness.orchestrator.refuse_claims_with = "already_claimed"

    wait_seconds = harness.dispatcher.run_once()

    # No lease, the stage is still free, and we ask again at once.
    assert harness.leases.ids() == []
    assert harness.stages[TaskKind.DOWNLOAD_RAW].is_idle
    assert wait_seconds == 0


def test_nothing_to_do_waits_as_told(harness):
    assert harness.dispatcher.run_once() == 1.0  # the fake says retry_after_s=1


def test_worker_near_cap_stops_asking_for_raws(harness):
    # The heartbeat answer says "near your cap" (design §9).
    harness.orchestrator.accepting_new_raws = False
    harness.heartbeater.beat_once()
    harness.dispatcher.run_once()
    # It still processes what it holds, but asks for no new raws.
    assert TaskKind.DOWNLOAD_RAW not in harness.orchestrator.next_requests[-1].accept_kinds
    assert TaskKind.PROCESS in harness.orchestrator.next_requests[-1].accept_kinds


def test_master_asks_for_raws_even_when_told_it_is_near_its_cap(tmp_path):
    # The master holds every raw (design §4): the near-cap signal does not
    # stop it; only real disk space does.
    harness = build_harness(tmp_path, role=WorkerRole.MASTER)
    harness.orchestrator.accepting_new_raws = False
    harness.heartbeater.beat_once()
    harness.dispatcher.run_once()
    assert TaskKind.DOWNLOAD_RAW in harness.orchestrator.next_requests[-1].accept_kinds


def test_raw_that_does_not_fit_under_the_cap_is_not_claimed(tmp_path):
    # Setup: a worker with a 1 GB cap is offered a 2 GB raw.
    harness = build_harness(tmp_path, cap_bytes=1 * GB)
    harness.orchestrator.offer(download_offer(size_bytes=2 * GB))

    harness.dispatcher.run_once()

    # Not claimed, and download_raw is left out until the next heartbeat.
    assert harness.leases.ids() == []
    harness.dispatcher.run_once()
    assert TaskKind.DOWNLOAD_RAW not in harness.orchestrator.next_requests[-1].accept_kinds


def test_master_takes_a_raw_over_its_cap_when_the_disk_has_room(tmp_path):
    harness = build_harness(tmp_path, role=WorkerRole.MASTER, cap_bytes=1 * GB)
    harness.orchestrator.offer(download_offer(size_bytes=2 * GB))
    harness.dispatcher.run_once()
    assert len(harness.leases.ids()) == 1


def test_master_refuses_a_raw_bigger_than_the_free_disk(tmp_path):
    # 5 GB free on disk, minus the 2 GB reserve, leaves room for 3 GB.
    harness = build_harness(tmp_path, role=WorkerRole.MASTER, disk_free_bytes=5 * GB)
    harness.orchestrator.offer(download_offer(size_bytes=4 * GB))
    harness.dispatcher.run_once()
    assert harness.leases.ids() == []
