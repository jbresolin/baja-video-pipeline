"""make_proxy: thumbnail + 480p proxy from a held raw, both uploaded (docs/api.md §5).

ffmpeg is faked (tests/worker/fakes.py): it records the real command line
from shared.ffmpeg and writes a small file in place of the video, so these
tests check the flow and the commands, not the pictures.
"""

from dataclasses import replace

from shared.media_probe import MediaInfo
from shared.models import FailureScope, MakeProxyTask, MeasurementKind
from tests.worker.fake_orchestrator import BASE_URL
from worker.media_tools import MediaToolError


def hold_raw(harness, raw_bytes):
    """Make the worker a holder: the job exists and its raw is in raws/."""
    job = harness.orchestrator.add_job(raw_bytes)
    harness.storage.raw_path(job.job_id, ".mp4").write_bytes(raw_bytes)
    return job


def proxy_task(job) -> MakeProxyTask:
    return MakeProxyTask(
        job_id=job.job_id,
        raw_sha256=job.sha256,
        proxy_upload_url=f"{BASE_URL}/jobs/{job.job_id}/proxy",
        thumbnail_upload_url=f"{BASE_URL}/jobs/{job.job_id}/thumbnail",
    )


def test_thumbnail_then_proxy_are_rendered_and_uploaded(harness, raw_bytes):
    job = hold_raw(harness, raw_bytes)

    harness.claim_and_run(proxy_task(job))

    # The orchestrator got the thumbnail (a single PUT)...
    assert harness.orchestrator.thumbnails[job.job_id].startswith(b"fake thumbnail")
    # ...and the full proxy through the chunked upload, verified by the
    # real ChunkedUploadReceiver.
    stored_proxy = harness.orchestrator.stored_upload("proxies", job.job_id)
    assert stored_proxy.read_bytes().startswith(b"fake proxy")
    # Order matters (contract): thumbnail first, because the proxy's last
    # chunk ends the lease.
    assert [what for what, _ in harness.media.commands] == ["thumbnail", "proxy"]
    # The temporary renders were cleaned out of tmp/.
    assert list(harness.storage.tmp_dir.iterdir()) == []
    # An upload measurement waits for the next heartbeat.
    assert [m.kind for m in harness.env.measurements.drain()] == [MeasurementKind.UPLOAD]


def test_hdr_raw_gets_a_tone_mapped_proxy(harness, raw_bytes):
    # Setup: ffprobe says the raw is HDR (PQ transfer, as iPhones record).
    harness.media.info = MediaInfo(12.0, True, 1920, 1080, color_transfer="smpte2084")
    job = hold_raw(harness, raw_bytes)

    harness.claim_and_run(proxy_task(job))

    # The proxy command contains the tonemap filter (design §15).
    video_filter = " ".join(harness.media.command_for("proxy"))
    assert "tonemap=tonemap=hable" in video_filter


def test_sdr_raw_proxy_has_no_tone_mapping(harness, raw_bytes):
    job = hold_raw(harness, raw_bytes)
    harness.claim_and_run(proxy_task(job))
    assert "tonemap" not in " ".join(harness.media.command_for("proxy"))


def test_short_clip_thumbnail_is_taken_halfway(harness, raw_bytes):
    # A 1-second clip has no frame at 1.0 s; the thumbnail moves to 0.5 s.
    harness.media.info = MediaInfo(1.0, True, 1920, 1080)
    job = hold_raw(harness, raw_bytes)
    harness.claim_and_run(proxy_task(job))
    thumbnail_command = harness.media.command_for("thumbnail")
    assert thumbnail_command[thumbnail_command.index("-ss") + 1] == "0.500"


def test_ffmpeg_error_is_reported_with_its_message_and_exit_code(harness, raw_bytes):
    job = hold_raw(harness, raw_bytes)
    harness.media.failing_render = "proxy"

    harness.claim_and_run(proxy_task(job))

    # The failure report carries ffmpeg's own words, for the reviewer.
    [(failed_job_id, failure)] = harness.orchestrator.failures
    assert failed_job_id == job.job_id
    assert "moov atom not found" in failure.message
    assert failure.exit_code == 1
    # ffprobe could read the raw, so this machine's ffmpeg is to blame:
    # scope "worker", and another holder gets the task (design §17).
    assert failure.scope == FailureScope.WORKER
    # Nothing was uploaded, and tmp/ is clean.
    assert job.job_id not in harness.orchestrator.thumbnails
    assert list(harness.storage.tmp_dir.iterdir()) == []


def test_missing_raw_is_reported_as_this_workers_problem(harness, raw_bytes):
    # The orchestrator thinks we hold the raw, but it is not on disk
    # (deleted by hand). That is not the video's fault: scope "worker", so
    # another holder makes the proxy; the next heartbeat's held_job_ids
    # also corrects the orchestrator's list of holders.
    job = harness.orchestrator.add_job(raw_bytes)
    harness.claim_and_run(proxy_task(job))
    [(_, failure)] = harness.orchestrator.failures
    assert failure.scope == FailureScope.WORKER
    assert harness.media.commands == []


def test_unreadable_raw_is_reported_as_the_jobs_problem(harness, raw_bytes):
    # ffprobe cannot read the raw: no worker could make a proxy of it, so
    # the job fails at once (scope "job").
    job = hold_raw(harness, raw_bytes)
    harness.media.probe_error = MediaToolError("ffprobe cannot read the file: EOF", 1)
    harness.claim_and_run(proxy_task(job))
    [(_, failure)] = harness.orchestrator.failures
    assert "ffprobe cannot read" in failure.message
    assert failure.scope == FailureScope.JOB


def test_every_proxy_chunk_carries_the_proxys_frame_rate(harness, raw_bytes):
    # Setup: small 4 KB chunks so the proxy goes up in several requests.
    # The fake ffprobe says the rendered proxy runs at 25 fps while the
    # raw runs at 29.97: the header must describe the proxy, because that
    # is what the review UI steps through.
    job = hold_raw(harness, raw_bytes)
    harness.media.proxy_info = MediaInfo(12.0, True, 854, 480, frame_rate=25.0)
    harness.env.settings = replace(harness.env.settings, upload_chunk_bytes=4096)

    harness.claim_and_run(proxy_task(job))

    # More than one chunk was sent, and every one said "Frame-Rate: 25".
    sent = harness.orchestrator.proxy_frame_rate_headers
    assert len(sent) > 1
    assert set(sent) == {"25"}
    # The worker really probed the rendered proxy (in tmp/), not just the raw.
    assert any(path.name.endswith(".proxy.mp4") for path in harness.media.probed_paths)


def test_ntsc_frame_rate_is_sent_as_a_short_decimal(harness, raw_bytes):
    # ffprobe reports NTSC video as 30000/1001 = 29.97002997…; the header
    # carries "29.97", the way docs/api.md §6.2 shows it.
    job = hold_raw(harness, raw_bytes)  # the fake's default info is 30000/1001 fps

    harness.claim_and_run(proxy_task(job))

    assert set(harness.orchestrator.proxy_frame_rate_headers) == {"29.97"}


def test_unknown_frame_rate_leaves_the_header_out(harness, raw_bytes):
    # When ffprobe cannot read the proxy, the frame rate is unknown. The
    # header is optional, so it is simply not sent; the proxy still goes up
    # and nothing is reported as failed.
    job = hold_raw(harness, raw_bytes)
    harness.media.proxy_probe_error = MediaToolError("ffprobe cannot read the file", 1)

    harness.claim_and_run(proxy_task(job))

    assert harness.orchestrator.proxy_frame_rate_headers
    assert set(harness.orchestrator.proxy_frame_rate_headers) == {None}
    assert harness.orchestrator.stored_upload("proxies", job.job_id).exists()
    assert harness.orchestrator.failures == []
