"""upload_original: send a held raw to the VPS because a reviewer asked for it.

LIVESTACK-41 item 15. The 480p proxy is too small to read a car number or
judge a close finish, so the job page has "Baixar original". The VPS no
longer has the raw (it is transit only), so the orchestrator offers this
task to a holder (the master first). The raw goes, chunked and resumable
like a clip, to the task's `upload_url`; the completing chunk ends the
lease. Nothing about the job changes, and the VPS copy never counts as a
holder.

Failures (worker.failures): the raw missing from our disk → reported with
scope "worker", so another holder is asked; network trouble after the
retries → reported the same way. The orchestrator never fails the job for
this task.
"""

import logging
import time

from shared.http_transfer import HashMismatchError, UploadFailedError, upload_file_in_chunks
from shared.models import MeasurementKind, UploadOriginalTask
from worker.failures import TaskFailedError, TransferGaveUpError
from worker.leases import REASON_LEASE_LOST, ActiveLease, CancelToken, TaskCancelledError
from worker.tasks.environment import TaskEnvironment

logger = logging.getLogger(__name__)


def run_upload_original(
    env: TaskEnvironment, lease: ActiveLease, task: UploadOriginalTask, cancel: CancelToken
) -> None:
    raw_path = env.storage.find_raw(task.job_id)
    if raw_path is None:
        raise TaskFailedError("the raw is not on this worker's disk")
    url = env.api.absolute_url(task.upload_url)
    logger.info("uploading the original for a reviewer (%d bytes)", task.size_bytes)
    started_at = time.monotonic()
    # Pinned: a discard's cleanup must not delete it under the upload.
    with env.storage.pin(task.job_id):
        try:
            result = upload_file_in_chunks(
                env.transfer_client(cancel),
                url,
                raw_path,
                sha256=task.raw_sha256,
                headers=env.api.upload_headers(url, lease.lease_id),
                chunk_size=env.settings.upload_chunk_bytes,
                retry_delay_s=env.settings.transfer_retry_delay_seconds,
                sleep=cancel.sleep,
            )
        except UploadFailedError as error:
            if error.error_code == "lease_lost":
                raise TaskCancelledError(REASON_LEASE_LOST) from error
            raise TransferGaveUpError(f"original upload failed: {error}") from error
        except HashMismatchError as error:
            raise TransferGaveUpError("the original arrived corrupted at the VPS") from error
    env.measurements.record(MeasurementKind.UPLOAD, result.size_bytes, started_at)
    logger.info("original uploaded", extra={"job_id": task.job_id})
