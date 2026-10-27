"""make_proxy: render thumbnail + 480p proxy from a held raw and upload both.

docs/api.md §5: probe the raw (HDR → tone-map), PUT the JPEG thumbnail,
then the chunked proxy upload; its completing chunk ends the lease.
Everything is rendered into tmp/ and deleted afterwards, whatever happens.

Every proxy chunk carries the Frame-Rate header (docs/api.md §6.2) with
the frame rate ffprobe reads from the rendered proxy; the review UI steps
single frames with it. When ffprobe cannot tell, the header is left out.
"""

import logging
import time
from pathlib import Path

from shared.http_transfer import HashMismatchError, UploadFailedError, upload_file_in_chunks
from shared.models import MakeProxyTask, MeasurementKind
from shared.protocol import FRAME_RATE_HEADER
from worker.failures import TaskFailedError, TransferGaveUpError, probe_input
from worker.leases import REASON_LEASE_LOST, ActiveLease, CancelToken, TaskCancelledError
from worker.media_tools import MediaToolError
from worker.tasks.environment import TaskEnvironment

logger = logging.getLogger(__name__)

# Grab the thumbnail 1 s in, or halfway through clips shorter than 2 s.
THUMBNAIL_AT_SECONDS = 1.0


def run_make_proxy(
    env: TaskEnvironment, lease: ActiveLease, task: MakeProxyTask, cancel: CancelToken
) -> None:
    raw_path = env.storage.find_raw(task.job_id)
    if raw_path is None:
        # The orchestrator thinks we hold it (the next heartbeat's
        # held_job_ids corrects that). Our problem, not the job's.
        raise TaskFailedError("the raw is not on this worker's disk")

    thumbnail_path = env.storage.tmp_path(f"{task.job_id}.thumb.jpg")
    proxy_path = env.storage.tmp_path(f"{task.job_id}.proxy.mp4")
    with env.storage.pin(task.job_id):
        try:
            _render(env, raw_path, thumbnail_path, proxy_path, cancel)
            frame_rate = proxy_frame_rate(env, proxy_path)
            _upload(env, lease, task, thumbnail_path, proxy_path, frame_rate, cancel)
        finally:
            thumbnail_path.unlink(missing_ok=True)
            proxy_path.unlink(missing_ok=True)


def _render(env: TaskEnvironment, raw_path, thumbnail_path, proxy_path, cancel) -> None:
    # An unreadable raw is the job's fault (UndecodableInputError); a
    # failing ffmpeg run is this worker's (MediaToolError). worker.failures
    # turns each into the right failure scope.
    info = probe_input(env.media, raw_path)
    at_seconds = THUMBNAIL_AT_SECONDS
    if info.duration_s is not None:
        at_seconds = min(THUMBNAIL_AT_SECONDS, info.duration_s / 2)
    env.media.make_thumbnail(raw_path, thumbnail_path, at_seconds, cancel)
    env.media.make_proxy(raw_path, proxy_path, info, cancel)
    logger.info(
        "proxy rendered (%d bytes, HDR tone-mapped: %s)", proxy_path.stat().st_size, info.is_hdr
    )


def proxy_frame_rate(env: TaskEnvironment, proxy_path: Path) -> float | None:
    """The rendered proxy's frames per second, or None when ffprobe cannot say.

    We probe the proxy itself rather than reuse the raw's rate, because the
    proxy is re-timed to a constant rate (shared.ffmpeg's proxy settings).
    """
    try:
        frame_rate = env.media.probe(proxy_path).frame_rate
    except MediaToolError as error:
        logger.warning("cannot read the proxy's frame rate (%s); sending none", error.message)
        return None
    if frame_rate is None:
        logger.warning("ffprobe reports no frame rate for the proxy; sending none")
    return frame_rate


def frame_rate_header_value(frame_rate: float) -> str:
    """29.97002997 → "29.97", 30.0 → "30": a short decimal string for the header."""
    return format(round(frame_rate, 3), "g")


def _upload(
    env: TaskEnvironment,
    lease,
    task: MakeProxyTask,
    thumbnail_path,
    proxy_path,
    frame_rate: float | None,
    cancel,
):
    cancel.raise_if_cancelled()
    # The contract asks for the thumbnail before the proxy, because the
    # proxy's completing chunk ends the lease.
    thumbnail_bytes = thumbnail_path.read_bytes()
    env.report(
        lambda: env.api.upload_thumbnail(
            task.thumbnail_upload_url, lease.lease_id, thumbnail_bytes
        ),
        cancel,
        "thumbnail upload",
    )

    proxy_url = env.api.absolute_url(task.proxy_upload_url)
    headers = env.api.upload_headers(proxy_url, lease.lease_id)
    if frame_rate is not None:
        headers[FRAME_RATE_HEADER] = frame_rate_header_value(frame_rate)
    started_at = time.monotonic()
    try:
        result = upload_file_in_chunks(
            env.transfer_client(cancel),
            proxy_url,
            proxy_path,
            headers=headers,
            chunk_size=env.settings.upload_chunk_bytes,
            retry_delay_s=env.settings.transfer_retry_delay_seconds,
            sleep=cancel.sleep,
        )
    except UploadFailedError as error:
        if error.error_code == "lease_lost":
            raise TaskCancelledError(REASON_LEASE_LOST) from error
        raise TransferGaveUpError(f"proxy upload failed: {error}") from error
    except HashMismatchError as error:
        raise TransferGaveUpError("the proxy arrived corrupted at the orchestrator") from error
    env.measurements.record(MeasurementKind.UPLOAD, result.size_bytes, started_at)
    logger.info("proxy uploaded (%d bytes in %d requests)", result.size_bytes, result.requests_sent)
