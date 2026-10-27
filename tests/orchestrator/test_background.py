"""Tests for the background sweepers, one pass at a time with the fake clock.

In production each runs every few seconds on asyncio (background/runner.py).
Here we call a single pass directly, so the tests are fast and exact.
"""

import asyncio
import os

from orchestrator.background.runner import PeriodicTask, run_periodically
from orchestrator.background.sweepers import (
    clean_inbox_once,
    clean_outbox_once,
    evict_proxies_once,
    expire_leases_once,
)
from orchestrator.testing.scenarios import (
    claim_ok,
    hold_raw,
    make_proxy,
    receive_job,
    review_to_ready,
)
from shared.models import TaskKind, WorkerRole


def test_lease_sweeper_frees_an_abandoned_download_slot(harness):
    harness.enroll(WorkerRole.MASTER, "master")
    worker = harness.enroll(WorkerRole.WORKER, "worker")
    job_id = receive_job(harness)
    lease_id, _ = claim_ok(harness, worker, job_id, TaskKind.DOWNLOAD_RAW)
    # Before the TTL nothing happens.
    harness.clock.advance(44)
    assert expire_leases_once(harness.context) == []
    # After it, the lease and the unconfirmed holder slot are both gone.
    harness.clock.advance(2)
    assert expire_leases_once(harness.context) == [lease_id]
    job = harness.job(job_id)
    assert job["leases"] == [] and job["holders"] == []


def test_lease_sweeper_says_when_a_ready_job_waits_for_an_offline_holder(harness):
    master = harness.enroll(WorkerRole.MASTER, "master")
    job_id = receive_job(harness)
    hold_raw(harness, master, job_id)
    make_proxy(harness, master, job_id)
    review_to_ready(harness, job_id)
    harness.clock.advance(60)  # the only holder went offline
    expire_leases_once(harness.context)
    assert "holder" in harness.job(job_id)["waiting_reason"]
    # It comes back: the sentence disappears.
    harness.heartbeat(master, held_job_ids=[job_id])
    expire_leases_once(harness.context)
    assert harness.job(job_id)["waiting_reason"] is None


def test_inbox_cleanup_deletes_raws_with_two_confirmed_holders(harness):
    master = harness.enroll(WorkerRole.MASTER, "master")
    other = harness.enroll(WorkerRole.WORKER, "other")
    job_id = receive_job(harness)
    inbox_file = harness.context.layout.inbox_path(job_id, ".mp4")
    hold_raw(harness, master, job_id)
    # One holder: the cleanup must keep the file.
    assert clean_inbox_once(harness.context) == []
    hold_raw(harness, other, job_id)
    # holder-confirm already deleted it; mark it back as present to check
    # the sweeper catches what holder-confirm might miss (e.g. a crash).
    inbox_file.write_bytes(b"left behind")
    with harness.context.database.transaction() as connection:
        connection.execute("UPDATE jobs SET raw_on_vps = 1 WHERE id = ?", (job_id,))
    assert clean_inbox_once(harness.context) == [job_id]
    assert not inbox_file.exists()
    assert harness.job(job_id)["raw_on_vps"] is False


def test_proxy_eviction_removes_oldest_finished_proxies_until_disk_is_free(harness):
    master = harness.enroll(WorkerRole.MASTER, "master")
    studio = harness.enroll(WorkerRole.STUDIO, "studio")
    finished = []
    for number in range(2):
        job_id = receive_job(harness, f"video {number}".encode() * 100)
        hold_raw(harness, master, job_id)
        make_proxy(harness, master, job_id)
        review_to_ready(harness, job_id, passagem=number + 1)
        claim_ok(harness, master, job_id, TaskKind.PROCESS)
        harness.client.post(
            f"/jobs/{job_id}/delivered",
            json={"sha256": "a" * 64, "size_bytes": 1, "relative_path": f"enduro/42/{number}.mp4"},
            headers=studio.headers,
        )
        finished.append(job_id)
        harness.clock.advance(10)
    still_in_review = receive_job(harness, b"in review" * 100)
    hold_raw(harness, master, still_in_review)
    make_proxy(harness, master, still_in_review)

    # Pretend the disk is short of space until one proxy file is gone.
    layout = harness.context.layout
    layout._free_bytes_reader = lambda path: (
        10**12 if not layout.proxy_path(finished[0]).exists() else 0
    )
    assert evict_proxies_once(harness.context) == [finished[0]]
    # The oldest finished proxy went; the newer one and the job still in
    # review kept theirs.
    assert harness.job(finished[0])["proxy_available"] is False
    assert harness.job(finished[1])["proxy_available"] is True
    assert harness.job(still_in_review)["proxy_available"] is True


def test_outbox_cleanup_deletes_files_nobody_needs(harness):
    outbox = harness.context.layout.outbox_dir
    orphan = outbox / f"{'b' * 32}.mp4"  # no such job
    orphan.write_bytes(b"x")
    old_partial = outbox / ".abandoned.mp4.part"
    old_partial.write_bytes(b"x")
    # Make the half-upload look two days old.
    two_days_ago = harness.clock.now().timestamp() - 2 * 86400
    os.utime(old_partial, (two_days_ago, two_days_ago))
    result = clean_outbox_once(harness.context)
    assert sorted(result.deleted_files) == sorted([orphan.name, old_partial.name])
    assert list(outbox.iterdir()) == []


def test_a_failing_pass_does_not_stop_the_periodic_loop(harness):
    # The runner calls run_once again and again. If one pass raises, it
    # must log it and carry on; a crashed loop would silently stop, e.g.,
    # leases from ever expiring.
    calls = []

    def flaky_pass(context):
        calls.append(len(calls))
        if len(calls) == 1:
            raise RuntimeError("first pass fails")

    async def run_three_passes():
        task = asyncio.create_task(
            run_periodically(PeriodicTask("flaky", 0, flaky_pass), harness.context)
        )
        while len(calls) < 3:
            await asyncio.sleep(0.01)
        task.cancel()

    asyncio.run(asyncio.wait_for(run_three_passes(), timeout=5))
    assert len(calls) >= 3
