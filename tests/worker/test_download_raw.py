"""download_raw: fetch the raw, verify it, confirm as holder (docs/api.md §5).

Every test offers a DownloadRawTask through the fake orchestrator and runs
it with `harness.claim_and_run`, which goes through the real dispatcher,
stage runner and handler. Partial files, resuming and lost leases are in
test_download_raw_resume.py.
"""

from shared.hashing import sha256_of_bytes
from shared.models import FailureScope, MeasurementKind, WorkerRole
from tests.worker.conftest import PEER_URL, build_harness
from tests.worker.download_offers import download_task
from tests.worker.fake_orchestrator import BEARER_TOKEN, TAILNET_URL
from worker.media_tools import MediaToolError


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


def test_inbox_on_the_tailnet_origin_from_enroll_gets_the_token(harness, raw_bytes):
    # On the VPS the inbox URL in offers starts with Caddy's tailnet
    # listener (tailnet_base_url from /enroll), which may differ from the
    # worker's orchestrator_url. It is the second origin allowed the token.
    job = harness.orchestrator.add_job(raw_bytes)
    task = download_task(job).model_copy(
        update={"download_url": f"{TAILNET_URL}/inbox/{job.job_id}.mp4"}
    )

    harness.claim_and_run(task)

    assert harness.storage.find_raw(job.job_id).read_bytes() == raw_bytes
    [(_, headers)] = harness.orchestrator.file_requests
    assert headers["authorization"] == f"Bearer {BEARER_TOKEN}"


def test_falls_back_to_a_peer_and_never_sends_it_our_token(harness, raw_bytes):
    # Setup: the inbox no longer has the file (the VPS deleted it after two
    # holders confirmed), but a peer holds it.
    job = harness.orchestrator.add_job(raw_bytes)
    harness.orchestrator.inbox.clear()
    harness.add_peer_holding(job.job_id, raw_bytes)
    task = download_task(job, fallbacks=[f"{PEER_URL}/raw/{job.job_id}"])

    harness.claim_and_run(task)

    # The raw came from the peer and is complete.
    assert harness.storage.find_raw(job.job_id).read_bytes() == raw_bytes
    assert len(harness.orchestrator.holder_confirms) == 1
    # The peer was asked, and no request to it carried the bearer token:
    # only the orchestrator/Caddy may see it.
    peer_requests = harness.network.headers_sent_to("peer.test")
    assert peer_requests
    assert all("authorization" not in headers for headers in peer_requests)


def test_master_takes_a_raw_from_a_peer_when_the_offer_has_no_vps_copy(tmp_path, raw_bytes):
    # Setup: the master was offline while two volunteers took the raw and
    # the VPS deleted its copy. The orchestrator now offers the master the
    # job with a peer as `download_url` and no fallbacks (design §17,
    # "master backfill"). This replaces the old manifest-based backfill.
    harness = build_harness(tmp_path, role=WorkerRole.MASTER)
    job = harness.orchestrator.add_job(raw_bytes)
    harness.orchestrator.inbox.clear()
    harness.add_peer_holding(job.job_id, raw_bytes)

    harness.claim_and_run(download_task(job, inbox=False))

    # The master holds a verified copy and told the orchestrator so.
    held = harness.storage.find_raw(job.job_id)
    assert held is not None and held.read_bytes() == raw_bytes
    assert len(harness.orchestrator.holder_confirms) == 1
    # A peer URL has no file extension, so the raw is stored as ".bin";
    # ffmpeg reads the content, not the name.
    assert held.name == f"{job.job_id}.bin"
    # And no request went anywhere with our token except the orchestrator.
    assert all("authorization" not in h for h in harness.network.headers_sent_to("peer.test"))


def test_no_source_reachable_is_reported_as_this_workers_problem(harness, raw_bytes):
    # Setup: inbox empty, and the only fallback peer is not reachable. The
    # orchestrator itself is fine.
    job = harness.orchestrator.add_job(raw_bytes)
    harness.orchestrator.inbox.clear()

    harness.claim_and_run(download_task(job, fallbacks=[f"{PEER_URL}/raw/{job.job_id}"]))

    # Network trouble after the retries is "this worker could not do it"
    # (design §17): reported with scope "worker", so the orchestrator
    # offers the download to someone else instead of waiting for the
    # lease to expire.
    [(_, failure)] = harness.orchestrator.failures
    assert failure.scope == FailureScope.WORKER
    assert harness.orchestrator.holder_confirms == []
    assert harness.storage.find_raw(job.job_id) is None
    assert harness.leases.ids() == []


def test_master_lets_the_lease_expire_instead_of_reporting_network_trouble(tmp_path, raw_bytes):
    # The master must end up holding every raw (design §4). A "worker"
    # report would exclude it from this job's download until a reviewer
    # acts, so for network trouble it lets the lease expire instead and
    # the orchestrator offers the download again later.
    harness = build_harness(tmp_path, role=WorkerRole.MASTER)
    job = harness.orchestrator.add_job(raw_bytes)
    harness.orchestrator.inbox.clear()

    harness.claim_and_run(download_task(job, fallbacks=[f"{PEER_URL}/raw/{job.job_id}"]))

    assert harness.orchestrator.failures == []
    assert harness.leases.ids() == []  # no longer renewed: it will expire


def test_wrong_hash_from_every_source_is_reported_as_the_jobs_problem(harness, raw_bytes):
    # Setup: the inbox file is corrupt (one byte changed), so its SHA-256
    # never matches what the job says.
    job = harness.orchestrator.add_job(raw_bytes)
    harness.orchestrator.inbox[f"{job.job_id}.mp4"] = b"X" + raw_bytes[1:]

    harness.claim_and_run(download_task(job))

    # Every worker would get the same wrong bytes: the job fails at once
    # (scope "job") and a human looks at it.
    [(failed_job_id, failure)] = harness.orchestrator.failures
    assert failed_job_id == job.job_id
    assert failure.scope == FailureScope.JOB
    assert "SHA-256" in failure.message
    # And no corrupt file was left behind, visible or partial.
    assert harness.storage.find_raw(job.job_id) is None
    assert list(harness.storage.raws_dir.iterdir()) == []


def test_wrong_hash_at_one_source_and_an_offline_peer_blames_the_network(harness, raw_bytes):
    # Only the inbox answered (with bad bytes); the peer could not be
    # asked. That does not prove the raw is bad everywhere, so the report
    # says "worker", not "job".
    job = harness.orchestrator.add_job(raw_bytes)
    harness.orchestrator.inbox[f"{job.job_id}.mp4"] = b"X" + raw_bytes[1:]

    harness.claim_and_run(download_task(job, fallbacks=[f"{PEER_URL}/raw/{job.job_id}"]))

    [(_, failure)] = harness.orchestrator.failures
    assert failure.scope == FailureScope.WORKER


def test_unreadable_video_is_deleted_and_reported_as_the_jobs_problem(harness, raw_bytes):
    # Setup: the bytes arrive fine, but ffprobe cannot read them.
    job = harness.orchestrator.add_job(raw_bytes)
    harness.media.probe_error = MediaToolError("ffprobe cannot read the file: Invalid data", 1)

    harness.claim_and_run(download_task(job))

    [(_, failure)] = harness.orchestrator.failures
    assert "not a readable video" in failure.message
    assert failure.scope == FailureScope.JOB
    assert harness.storage.find_raw(job.job_id) is None


def test_disk_error_while_downloading_is_reported_as_this_workers_problem(
    harness, raw_bytes, monkeypatch
):
    # A full disk shows up in Python as OSError ("No space left on
    # device") while writing. That is this machine's problem: scope "worker".
    job = harness.orchestrator.add_job(raw_bytes)

    def disk_full(*args, **kwargs):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr("worker.tasks.download_raw.download_file", disk_full)

    harness.claim_and_run(download_task(job))

    [(_, failure)] = harness.orchestrator.failures
    assert failure.scope == FailureScope.WORKER
    assert "No space left on device" in failure.message


def test_raw_already_on_disk_is_not_downloaded_again(harness, raw_bytes):
    # Setup: a crash happened after the download finished but before
    # holder-confirm reached the orchestrator.
    job = harness.orchestrator.add_job(raw_bytes)
    harness.storage.raw_path(job.job_id, ".mp4").write_bytes(raw_bytes)

    harness.claim_and_run(download_task(job))

    # No byte was fetched, but holder-confirm was sent.
    assert harness.orchestrator.file_requests == []
    assert len(harness.orchestrator.holder_confirms) == 1
