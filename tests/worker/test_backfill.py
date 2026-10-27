"""Master backfill: copy raws missed while offline from peers (design §4)."""

import threading

from shared.models import Holder, HolderStatus, ManifestEntry, WorkerRole, utc_now
from tests.worker.conftest import build_harness
from worker.backfill import MasterBackfill
from worker.cancellable_transport import transfer_client_for
from worker.leases import CancelToken


def delivered_entry(job) -> ManifestEntry:
    return ManifestEntry(
        job_id=job.job_id,
        prova="Enduro",
        car_number=1,
        team_name=None,
        passagem=1,
        relative_path="enduro/1/1.mp4",
        sha256="c" * 64,
        size_bytes=10,
        delivered_at=utc_now(),
    )


def master_with_backfill(tmp_path):
    harness = build_harness(tmp_path, role=WorkerRole.MASTER)
    backfill = MasterBackfill(
        api=harness.env.api,
        storage=harness.storage,
        measurements=harness.env.measurements,
        transfer_client=transfer_client_for(harness.network, CancelToken(None, threading.Event())),
        own_worker_id="worker-1",
    )
    return harness, backfill


def test_delivered_job_missing_here_is_copied_from_a_holder_peer(tmp_path, raw_bytes):
    # Setup: while the master was offline, a job went all the way to
    # "delivered". Its raw is held by peer-1 only; the VPS copy is gone.
    harness, backfill = master_with_backfill(tmp_path)
    job = harness.orchestrator.add_job(
        raw_bytes,
        original_filename="VID_0042.MOV",
        holders=[Holder(worker_id="peer-1", status=HolderStatus.CONFIRMED, assigned_at=utc_now())],
    )
    harness.orchestrator.inbox.clear()
    harness.orchestrator.manifest_entries = [delivered_entry(job)]
    harness.add_peer_holding(job.job_id, raw_bytes)

    fetched = backfill.run_once(CancelToken(None, threading.Event()))

    # The master now holds the raw, with the original extension, verified.
    assert fetched == 1
    held = harness.storage.find_raw(job.job_id)
    assert held.name == f"{job.job_id}.mov" and held.read_bytes() == raw_bytes
    # The next heartbeat tells the orchestrator (no lease, so no holder-confirm).
    harness.heartbeater.beat_once()
    assert job.job_id in harness.orchestrator.heartbeats[-1].held_job_ids


def test_jobs_already_held_are_skipped(tmp_path, raw_bytes):
    harness, backfill = master_with_backfill(tmp_path)
    job = harness.orchestrator.add_job(raw_bytes)
    harness.storage.raw_path(job.job_id, ".mp4").write_bytes(raw_bytes)
    harness.orchestrator.manifest_entries = [delivered_entry(job)]
    assert backfill.missing_job_ids() == []


def test_no_online_holder_means_nothing_fetched(tmp_path, raw_bytes):
    harness, backfill = master_with_backfill(tmp_path)
    job = harness.orchestrator.add_job(
        raw_bytes,
        holders=[Holder(worker_id="gone", status=HolderStatus.CONFIRMED, assigned_at=utc_now())],
    )
    harness.orchestrator.manifest_entries = [delivered_entry(job)]
    assert backfill.run_once(CancelToken(None, threading.Event())) == 0
    assert harness.storage.find_raw(job.job_id) is None
