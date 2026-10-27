"""download_raw: partial files survive, and a later offer resumes them (design §17).

A download writes into the hidden "raws/.<job>.<ext>.part" and renames it
only when the SHA-256 matched. Whatever stops the task early (a crash, a
shutdown, a lost lease) leaves that partial file in place, so if the same
job is offered again the worker asks only for the missing bytes
("Range: bytes=<size>-"). Partial files nobody resumed for 24 h are
deleted when the worker starts (see also test_storage.py).
"""

import os
import time

from shared.http_transfer import partial_path_for
from tests.worker.download_offers import download_task, streaming_inbox
from tests.worker.fake_orchestrator import BASE_URL


def test_download_resumes_from_the_partial_file_after_a_crash(harness, raw_bytes):
    # Setup: an earlier run crashed after receiving the first 80,000 bytes.
    # They sit in the hidden partial file, exactly where download_file
    # leaves them.
    job = harness.orchestrator.add_job(raw_bytes)
    destination = harness.storage.raw_path(job.job_id, ".mp4")
    partial_path_for(destination).write_bytes(raw_bytes[:80_000])

    # The restarted worker is offered the same download again.
    harness.claim_and_run(download_task(job))

    # It asked only for the missing bytes...
    [(_, headers)] = harness.orchestrator.file_requests
    assert headers["range"] == "bytes=80000-"
    # ...and still ended with the complete, verified raw.
    assert destination.read_bytes() == raw_bytes
    assert len(harness.orchestrator.holder_confirms) == 1
    # The measurement counts only the bytes that crossed the network now.
    [measurement] = harness.env.measurements.drain()
    assert measurement.byte_count == len(raw_bytes) - 80_000


def test_lease_lost_mid_download_keeps_the_partial_file(harness, raw_bytes):
    # Setup: while the first piece is arriving, a heartbeat reports the
    # lease as lost (it expired, or the orchestrator gave the job to
    # someone else).
    job = harness.orchestrator.add_job(raw_bytes)

    def lose_lease():
        harness.orchestrator.revoke_lease(harness.leases.ids()[0])
        harness.heartbeater.beat_once()

    # Only /inbox/* is replaced by the slow streaming file; the API stays.
    harness.network.override_path(BASE_URL, "/inbox/", streaming_inbox(raw_bytes, lose_lease))

    harness.claim_and_run(download_task(job))

    # The task stopped: no holder-confirm, no failure report...
    assert harness.orchestrator.holder_confirms == []
    assert harness.orchestrator.failures == []
    # ...no visible (= complete) raw, but the bytes received so far are
    # kept in the hidden partial file for a later offer of the same job.
    assert harness.storage.find_raw(job.job_id) is None
    partial = partial_path_for(harness.storage.raw_path(job.job_id, ".mp4"))
    assert partial.exists() and 0 < partial.stat().st_size < len(raw_bytes)


def test_reoffer_after_a_lost_lease_resumes_and_fetches_fewer_bytes(harness, raw_bytes):
    # Part 1: as above, the lease is lost after the first piece arrived.
    job = harness.orchestrator.add_job(raw_bytes)

    def lose_lease():
        harness.orchestrator.revoke_lease(harness.leases.ids()[0])
        harness.heartbeater.beat_once()

    harness.network.override_path(BASE_URL, "/inbox/", streaming_inbox(raw_bytes, lose_lease))
    harness.claim_and_run(download_task(job))
    partial = partial_path_for(harness.storage.raw_path(job.job_id, ".mp4"))
    bytes_kept = partial.stat().st_size

    # Part 2: later the orchestrator offers the same job to us again, and
    # the inbox behaves normally this time.
    harness.network.remove_override(BASE_URL, "/inbox/")
    harness.env.measurements.drain()  # forget part 1's numbers
    harness.claim_and_run(download_task(job))

    # The worker asked only for what it did not have yet...
    [(_, headers)] = harness.orchestrator.file_requests
    assert headers["range"] == f"bytes={bytes_kept}-"
    # ...so fewer bytes crossed the network than the whole file...
    [measurement] = harness.env.measurements.drain()
    assert measurement.byte_count == len(raw_bytes) - bytes_kept < len(raw_bytes)
    # ...and the finished raw is complete and confirmed.
    assert harness.storage.find_raw(job.job_id).read_bytes() == raw_bytes
    assert len(harness.orchestrator.holder_confirms) == 1


def test_reoffer_from_a_peer_resumes_the_partial_from_the_inbox(harness, raw_bytes):
    # Setup: an earlier attempt got 50,000 bytes from the inbox into
    # ".<job>.mp4.part". Now the VPS copy is gone and the new offer lists
    # only a peer, whose URL ("…/raw/<job>") has no extension. The worker
    # must still continue the same partial file, not start a ".bin" one.
    job = harness.orchestrator.add_job(raw_bytes)
    harness.orchestrator.inbox.clear()
    harness.add_peer_holding(job.job_id, raw_bytes)
    mp4_destination = harness.storage.raw_path(job.job_id, ".mp4")
    partial_path_for(mp4_destination).write_bytes(raw_bytes[:50_000])

    harness.claim_and_run(download_task(job, inbox=False))

    # The peer was asked only for the rest, and the raw kept its ".mp4" name.
    peer_headers = harness.network.headers_sent_to("peer.test")
    assert peer_headers[0]["range"] == "bytes=50000-"
    assert harness.storage.find_raw(job.job_id) == mp4_destination
    assert mp4_destination.read_bytes() == raw_bytes


def test_a_bad_partial_file_is_not_blamed_on_the_source(harness, raw_bytes):
    # Setup: an old partial file holds wrong bytes (say, from a source that
    # has since been replaced). Resuming on top of it gives a wrong hash.
    # The worker must try once more from byte 0 before calling the source
    # bad, and here the fresh download is fine.
    job = harness.orchestrator.add_job(raw_bytes)
    destination = harness.storage.raw_path(job.job_id, ".mp4")
    partial_path_for(destination).write_bytes(b"garbage" * 1000)

    harness.claim_and_run(download_task(job))

    # Two requests: the resume (with Range) and the fresh one (without).
    ranges = [headers.get("range") for _, headers in harness.orchestrator.file_requests]
    assert ranges == ["bytes=7000-", None]
    # The raw is right and no failure was reported.
    assert destination.read_bytes() == raw_bytes
    assert harness.orchestrator.failures == []


def test_shutdown_mid_download_keeps_the_partial_file_for_next_time(harness, raw_bytes):
    # Setup: SIGTERM (shutdown) arrives while the first piece is arriving.
    job = harness.orchestrator.add_job(raw_bytes)
    inbox = streaming_inbox(raw_bytes, harness.shutdown.set)
    harness.network.override_path(BASE_URL, "/inbox/", inbox)

    harness.claim_and_run(download_task(job))

    # Nothing was reported, and the bytes received so far are still in
    # the hidden partial file, so the next start resumes instead of
    # downloading everything again. No visible (= complete) raw exists.
    assert harness.orchestrator.holder_confirms == []
    partial = partial_path_for(harness.storage.raw_path(job.job_id, ".mp4"))
    assert partial.exists() and 0 < partial.stat().st_size < len(raw_bytes)
    assert harness.storage.find_raw(job.job_id) is None


def test_partials_untouched_for_a_day_are_deleted_at_start_fresh_ones_kept(harness):
    # Setup: two partial downloads (each with its ".part.json" companion,
    # as the contract allows). One was last written 25 hours ago, the
    # other a minute ago.
    raws = harness.storage.raws_dir
    old_files = [raws / ".old-job.mp4.part", raws / ".old-job.mp4.part.json"]
    fresh_files = [raws / ".fresh-job.mp4.part", raws / ".fresh-job.mp4.part.json"]
    for path in old_files + fresh_files:
        path.write_bytes(b"x")
    twenty_five_hours_ago = time.time() - 25 * 3600
    for path in old_files:
        os.utime(path, (twenty_five_hours_ago, twenty_five_hours_ago))

    # The worker starts (Daemon.start calls this first).
    harness.storage.clean_up_at_start()

    # The day-old ones are gone; the fresh ones stay to be resumed.
    assert not any(path.exists() for path in old_files)
    assert all(path.exists() for path in fresh_files)
