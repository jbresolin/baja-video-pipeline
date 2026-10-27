"""Heartbeat: what the worker reports, and how it acts on the answer (docs/api.md §5)."""

import time

from shared.models import Lease, MeasurementKind, TaskKind, WorkerRole, utc_now
from tests.worker.conftest import build_harness


def fake_lease(lease_id: str, job_id: str = "job-1") -> Lease:
    now = utc_now()
    return Lease(
        lease_id=lease_id,
        job_id=job_id,
        worker_id="worker-1",
        task_kind=TaskKind.PROCESS,
        acquired_at=now,
        expires_at=now,
    )


def test_heartbeat_reports_leases_held_raws_space_and_measurements(harness):
    # Setup: two raws on disk (one complete, one still downloading), one
    # lease we work under, and one finished measurement.
    harness.storage.raw_path("job-a", ".mp4").write_bytes(b"a" * 1000)
    harness.storage.raws_dir.joinpath(".job-b.mp4.part").write_bytes(b"b" * 500)
    harness.leases.add(fake_lease("lease-1"))
    harness.orchestrator.leases["lease-1"] = fake_lease("lease-1")
    harness.env.measurements.record(MeasurementKind.DOWNLOAD, 1000, time.monotonic() - 2)

    harness.heartbeater.beat_once()

    [sent] = harness.orchestrator.heartbeats
    # Listing the lease is what renews it.
    assert sent.current_lease_ids == ["lease-1"]
    # Only complete raws count as held; the partial one is not ours yet.
    assert sent.held_job_ids == ["job-a"]
    # Free space = cap (10 GB) minus everything in the working folder,
    # partial files included.
    assert sent.free_bytes == 10 * 1024**3 - 1500
    assert sent.storage_cap_bytes == 10 * 1024**3
    assert sent.tailnet_address == "worker.test" and sent.file_server_port == 8710
    assert sent.receiver_port is None  # only studios have a receiver
    [measurement] = sent.measurements
    assert measurement.kind == MeasurementKind.DOWNLOAD and measurement.byte_count == 1000


def test_lost_lease_is_flagged_for_its_task(harness):
    lease = harness.leases.add(fake_lease("lease-1"))
    # The orchestrator no longer knows this lease (expired or revoked).
    harness.heartbeater.beat_once()
    # The task under it sees the "lost" flag at its next check.
    assert lease.lost.is_set()


def test_renewed_lease_is_not_flagged(harness):
    lease = harness.leases.add(fake_lease("lease-1"))
    harness.orchestrator.leases["lease-1"] = fake_lease("lease-1")
    harness.heartbeater.beat_once()
    assert not lease.lost.is_set()


def test_failed_heartbeat_keeps_measurements_for_the_next_one(harness):
    harness.env.measurements.record(MeasurementKind.UPLOAD, 5000, time.monotonic() - 1)
    harness.orchestrator.heartbeats_to_fail = 1  # the first heartbeat gets a 503

    assert harness.heartbeater.beat_once() is None
    harness.heartbeater.beat_once()

    # The measurement was not lost with the failed heartbeat.
    [sent] = harness.orchestrator.heartbeats
    assert [m.byte_count for m in sent.measurements] == [5000]


def test_deletable_raws_are_deleted_but_not_while_in_use(harness):
    # Setup: two raws of discarded jobs; one is being used by a task.
    harness.storage.raw_path("discarded-1", ".mp4").write_bytes(b"x")
    harness.storage.raw_path("discarded-2", ".mp4").write_bytes(b"y")
    harness.orchestrator.deletable_job_ids = ["discarded-1", "discarded-2"]

    with harness.storage.pin("discarded-2"):
        harness.heartbeater.beat_once()
        # The free one is gone; the one in use stays for now.
        assert harness.storage.held_job_ids() == ["discarded-2"]

    # The next heartbeat (after the task finished) deletes it too.
    harness.heartbeater.beat_once()
    assert harness.storage.held_job_ids() == []


def test_accepting_new_raws_follows_the_answer(harness):
    harness.orchestrator.accepting_new_raws = False
    harness.heartbeater.beat_once()
    assert harness.heartbeater.accepting_new_raws is False
    harness.orchestrator.accepting_new_raws = True
    harness.heartbeater.beat_once()
    assert harness.heartbeater.accepting_new_raws is True


def test_studio_heartbeat_has_receiver_port_and_no_raws(tmp_path):
    harness = build_harness(tmp_path, role=WorkerRole.STUDIO)
    harness.heartbeater.beat_once()
    [sent] = harness.orchestrator.heartbeats
    assert sent.receiver_port == 8720 and sent.file_server_port is None
    assert sent.held_job_ids == []
