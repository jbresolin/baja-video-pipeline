"""download_raw: fetch the raw, verify it, confirm as holder (docs/api.md §5).

Every test offers a DownloadRawTask through the fake orchestrator and runs
it with `harness.claim_and_run`, which goes through the real dispatcher,
stage runner and handler.
"""

import httpx

from shared.hashing import sha256_of_bytes
from shared.http_transfer import partial_path_for
from shared.models import DownloadRawTask, MeasurementKind
from tests.worker.conftest import PEER_URL
from tests.worker.fake_orchestrator import BASE_URL, BEARER_TOKEN
from worker.media_tools import MediaToolError


def download_task(job, *, inbox: bool = True, fallbacks=()) -> DownloadRawTask:
    """The offer the orchestrator would send for `job`."""
    return DownloadRawTask(
        job_id=job.job_id,
        sha256=job.sha256,
        size_bytes=len(job.raw_bytes),
        download_url=f"{BASE_URL}/inbox/{job.job_id}.mp4" if inbox else f"{PEER_URL}/raw/x",
        fallback_download_urls=list(fallbacks),
    )


def test_raw_is_downloaded_from_the_inbox_and_holder_confirmed(harness, raw_bytes):
    # Setup: one job whose raw sits in the VPS inbox.
    job = harness.orchestrator.add_job(raw_bytes)

    harness.claim_and_run(download_task(job))

    # The raw is now in raws/ under its job id, byte for byte.
    held = harness.storage.find_raw(job.job_id)
    assert held is not None and held.read_bytes() == raw_bytes
    # The worker told the orchestrator it holds the raw, with the hash,
    # the size and what ffprobe said (duration, audio).
    [(confirmed_job_id, confirm)] = harness.orchestrator.holder_confirms
    assert confirmed_job_id == job.job_id
    assert confirm.sha256 == sha256_of_bytes(raw_bytes)
    assert confirm.size_bytes == len(raw_bytes)
    assert confirm.duration_s == 12.0 and confirm.has_audio is True
    # The inbox (Caddy) needs our bearer token, so it must have been sent.
    [(path, headers)] = harness.orchestrator.file_requests
    assert headers["authorization"] == f"Bearer {BEARER_TOKEN}"
    # A download measurement waits for the next heartbeat.
    [measurement] = harness.env.measurements.drain()
    assert measurement.kind == MeasurementKind.DOWNLOAD
    assert measurement.byte_count == len(raw_bytes)


def test_falls_back_to_a_peer_and_never_sends_it_our_token(harness, raw_bytes):
    # Setup: the inbox no longer has the file (the VPS deleted it after two
    # holders confirmed), but a peer holds it.
    job = harness.orchestrator.add_job(raw_bytes)
    harness.orchestrator.inbox.clear()
    harness.add_peer_holding(job.job_id, raw_bytes)
    peer_requests = []
    real_handle = harness.network.handle_request

    def spy(request: httpx.Request) -> httpx.Response:
        # Remember the headers of every request that goes to the peer.
        if request.url.host == "peer.test":
            peer_requests.append(dict(request.headers))
        return real_handle(request)

    harness.network.handle_request = spy
    task = download_task(job, fallbacks=[f"{PEER_URL}/raw/{job.job_id}"])

    harness.claim_and_run(task)

    # The raw came from the peer and is complete.
    assert harness.storage.find_raw(job.job_id).read_bytes() == raw_bytes
    assert len(harness.orchestrator.holder_confirms) == 1
    # The peer was asked, and no request to it carried the bearer token:
    # only the orchestrator/Caddy may see it.
    assert peer_requests
    assert all("authorization" not in headers for headers in peer_requests)


def test_peer_offline_and_inbox_gone_abandons_without_reporting_failure(harness, raw_bytes):
    # Setup: inbox empty, and the only fallback peer is not reachable.
    job = harness.orchestrator.add_job(raw_bytes)
    harness.orchestrator.inbox.clear()

    harness.claim_and_run(download_task(job, fallbacks=[f"{PEER_URL}/raw/{job.job_id}"]))

    # Network trouble is not a failure (docs/api.md §5): nothing reported,
    # no raw on disk, and our lease is no longer renewed (it will expire
    # and the orchestrator offers the download again later).
    assert harness.orchestrator.failures == []
    assert harness.orchestrator.holder_confirms == []
    assert harness.storage.find_raw(job.job_id) is None
    assert harness.leases.ids() == []


def test_wrong_hash_from_every_source_is_reported_as_failed(harness, raw_bytes):
    # Setup: the inbox file is corrupt (one byte changed), so its SHA-256
    # never matches what the job says.
    job = harness.orchestrator.add_job(raw_bytes)
    harness.orchestrator.inbox[f"{job.job_id}.mp4"] = b"X" + raw_bytes[1:]

    harness.claim_and_run(download_task(job))

    # Retrying the same bytes cannot help, so a human must look: failed.
    [(failed_job_id, failure)] = harness.orchestrator.failures
    assert failed_job_id == job.job_id
    assert "SHA-256" in failure.message
    # And no corrupt file was left behind, visible or partial.
    assert harness.storage.find_raw(job.job_id) is None
    assert list(harness.storage.raws_dir.iterdir()) == []


def test_unreadable_video_is_deleted_and_reported(harness, raw_bytes):
    # Setup: the bytes arrive fine, but ffprobe cannot read them.
    job = harness.orchestrator.add_job(raw_bytes)
    harness.media.probe_error = MediaToolError("ffprobe cannot read the file: Invalid data", 1)

    harness.claim_and_run(download_task(job))

    [(_, failure)] = harness.orchestrator.failures
    assert "not a readable video" in failure.message
    assert harness.storage.find_raw(job.job_id) is None


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


def test_raw_already_on_disk_is_not_downloaded_again(harness, raw_bytes):
    # Setup: a crash happened after the download finished but before
    # holder-confirm reached the orchestrator.
    job = harness.orchestrator.add_job(raw_bytes)
    harness.storage.raw_path(job.job_id, ".mp4").write_bytes(raw_bytes)

    harness.claim_and_run(download_task(job))

    # No byte was fetched, but holder-confirm was sent.
    assert harness.orchestrator.file_requests == []
    assert len(harness.orchestrator.holder_confirms) == 1


def streaming_inbox(content: bytes, after_first_piece) -> httpx.MockTransport:
    """An inbox that sends the file in 10 KB pieces and calls `after_first_piece`
    once the first piece is out, to simulate something happening mid-download."""

    def handler(request: httpx.Request) -> httpx.Response:
        def pieces():
            for start in range(0, len(content), 10_000):
                yield content[start : start + 10_000]
                if start == 0:
                    after_first_piece()

        return httpx.Response(200, content=pieces())

    return httpx.MockTransport(handler)


def test_lease_lost_mid_download_stops_and_discards_the_partial_file(harness, raw_bytes):
    # Setup: while the first piece is arriving, a heartbeat reports the
    # lease as lost (for example, a reviewer discarded the job).
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
    # ...and docs/api.md §5 says to discard partial output on "lost".
    assert list(harness.storage.raws_dir.iterdir()) == []


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
