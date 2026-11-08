"""upload_clip: send a finished clip to the VPS because a reviewer asked for it.

LIVESTACK-52, "Baixar clipe". The clip lives only on the worker that
rendered it, the master's archive and the studio; the orchestrator offers
this task to one of them (the master first, the studio only as a last
resort) and the clip goes, chunked and resumable like any clip, to the
task's `upload_url`. The completing chunk ends the lease. Nothing about the
job changes.

Where the clip is looked for, by its SHA-256 (the one the reviewer asked
for, task.sha256):
  1. the clips this PC kept after rendering them (worker.clip_store);
  2. the master's archive, at task.archive_relative_path;
  3. on the studio, its clip folder, under task.studio_relative_path or
     another version name of the same passagem (a locked clip on air makes
     the studio store a new one as "-v3").
Not found → reported with scope "worker", so the next PC in line is asked.
"""

import logging
import time
from pathlib import Path

from shared.hashing import sha256_of_file
from shared.http_transfer import HashMismatchError, UploadFailedError, upload_file_in_chunks
from shared.layout import unversioned_clip_relative_path
from shared.models import MeasurementKind, UploadClipTask
from worker.failures import TaskFailedError, TransferGaveUpError
from worker.leases import REASON_LEASE_LOST, ActiveLease, CancelToken, TaskCancelledError
from worker.tasks.environment import TaskEnvironment

logger = logging.getLogger(__name__)


def find_clip(env: TaskEnvironment, task: UploadClipTask) -> Path | None:
    """This PC's copy of exactly that clip, or None."""
    kept = env.storage.clips.find(task.job_id, task.sha256)
    if kept is not None:
        return kept  # its name carries the SHA-256 it was verified with
    try:
        archived = env.storage.clips.archive_path(task.archive_relative_path)
    except ValueError:
        archived = None
    if archived is not None and archived.is_file() and sha256_of_file(archived) == task.sha256:
        return archived
    library = env.studio_library
    if library is not None:
        first_name = unversioned_clip_relative_path(task.studio_relative_path)
        found = library.find_same_content(first_name, task.sha256)
        if found is not None:
            return library.absolute_path(found)
    return None


def run_upload_clip(
    env: TaskEnvironment, lease: ActiveLease, task: UploadClipTask, cancel: CancelToken
) -> None:
    clip_path = find_clip(env, task)
    if clip_path is None:
        raise TaskFailedError("the clip asked for is not on this PC")
    url = env.api.absolute_url(task.upload_url)
    logger.info(
        "uploading clip version %d for a reviewer (%d bytes)", task.version, task.size_bytes
    )
    started_at = time.monotonic()
    try:
        result = upload_file_in_chunks(
            env.transfer_client(cancel),
            url,
            clip_path,
            sha256=task.sha256,
            headers=env.api.upload_headers(url, lease.lease_id),
            chunk_size=env.settings.upload_chunk_bytes,
            retry_delay_s=env.settings.transfer_retry_delay_seconds,
            sleep=cancel.sleep,
        )
    except UploadFailedError as error:
        if error.error_code == "lease_lost":
            raise TaskCancelledError(REASON_LEASE_LOST) from error
        raise TransferGaveUpError(f"clip upload failed: {error}") from error
    except HashMismatchError as error:
        raise TransferGaveUpError("the clip arrived corrupted at the VPS") from error
    env.measurements.record(MeasurementKind.UPLOAD, result.size_bytes, started_at)
    logger.info("clip uploaded for download", extra={"job_id": task.job_id})
