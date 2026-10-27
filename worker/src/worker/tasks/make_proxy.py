"""make_proxy: render thumbnail + 480p proxy from a held raw and upload both.

docs/api.md §5: probe the raw (HDR → tone-map), PUT the JPEG thumbnail,
then the chunked proxy upload; its completing chunk ends the lease.
Everything is rendered into tmp/ and deleted afterwards, whatever happens.
"""

import logging
import time

from shared.http_transfer import HashMismatchError, UploadFailedError, upload_file_in_chunks
from shared.models import MakeProxyTask, MeasurementKind
from worker.leases import REASON_LEASE_LOST, ActiveLease, CancelToken, TaskCancelledError
from worker.media_tools import MediaToolError
from worker.tasks.environment import TaskAbandonedError, TaskEnvironment, TaskFailedError

logger = logging.getLogger(__name__)

# Grab the thumbnail 1 s in, or halfway through clips shorter than 2 s.
THUMBNAIL_AT_SECONDS = 1.0


def run_make_proxy(
    env: TaskEnvironment, lease: ActiveLease, task: MakeProxyTask, cancel: CancelToken
) -> None:
    raw_path = env.storage.find_raw(task.job_id)
    if raw_path is None:
        # The orchestrator thinks we hold it; the next heartbeat's
        # held_job_ids will correct that.
        raise TaskAbandonedError("raw is not on this worker's disk")

    thumbnail_path = env.storage.tmp_path(f"{task.job_id}.thumb.jpg")
    proxy_path = env.storage.tmp_path(f"{task.job_id}.proxy.mp4")
    with env.storage.pin(task.job_id):
        try:
            _render(env, raw_path, thumbnail_path, proxy_path, cancel)
            _upload(env, lease, task, thumbnail_path, proxy_path, cancel)
        finally:
            thumbnail_path.unlink(missing_ok=True)
            proxy_path.unlink(missing_ok=True)


def _render(env: TaskEnvironment, raw_path, thumbnail_path, proxy_path, cancel) -> None:
    try:
        info = env.media.probe(raw_path)
        at_seconds = THUMBNAIL_AT_SECONDS
        if info.duration_s is not None:
            at_seconds = min(THUMBNAIL_AT_SECONDS, info.duration_s / 2)
        env.media.make_thumbnail(raw_path, thumbnail_path, at_seconds, cancel)
        env.media.make_proxy(raw_path, proxy_path, info, cancel)
    except MediaToolError as error:
        raise TaskFailedError(error.message, error.exit_code) from error
    logger.info(
        "proxy rendered (%d bytes, HDR tone-mapped: %s)", proxy_path.stat().st_size, info.is_hdr
    )


def _upload(env: TaskEnvironment, lease, task: MakeProxyTask, thumbnail_path, proxy_path, cancel):
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

    started_at = time.monotonic()
    try:
        result = upload_file_in_chunks(
            env.transfer_client(cancel),
            env.api.absolute_url(task.proxy_upload_url),
            proxy_path,
            headers=env.api.upload_headers(lease.lease_id),
            chunk_size=env.settings.upload_chunk_bytes,
            sleep=cancel.sleep,
        )
    except UploadFailedError as error:
        if error.error_code == "lease_lost":
            raise TaskCancelledError(REASON_LEASE_LOST) from error
        raise TaskAbandonedError(f"proxy upload failed: {error}") from error
    except HashMismatchError as error:
        raise TaskAbandonedError("proxy arrived corrupted; will retry") from error
    env.measurements.record(MeasurementKind.UPLOAD, result.size_bytes, started_at)
    logger.info("proxy uploaded (%d bytes in %d requests)", result.size_bytes, result.requests_sent)
