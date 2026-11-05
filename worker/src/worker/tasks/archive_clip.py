"""archive_clip (master only): keep one finished clip version in the archive folder.

docs/api.md §5 and design §11 as amended. The task names the clip
(job, version, sha256) and where it goes, clips/<prova>/<car>/<n>[.vN].mp4
(worker.clip_store). Sources, in this order:
  1. the master's own kept copy (it rendered the clip): moved, not copied;
  2. each URL in `download_urls`: other workers' file servers (:8710, no
     token) and the VPS outbox (Caddy, with our token; worker.token_origins
     decides), with shared.http_transfer.download_file (Range resume,
     SHA-256 check, rename only when the hash matched).
Then POST /jobs/{id}/clip-archived with the sha256, which lets the workers
evict their copies and the VPS drop its outbox copy.

When no source works (network), the lease is left to expire and the
orchestrator offers the clip again later (worker.failures).
"""

import logging
import time
from pathlib import Path

import httpx

from shared.hashing import sha256_of_file
from shared.http_transfer import DownloadFailedError, HashMismatchError, download_file
from shared.models import ArchiveClipTask, ClipArchivedRequest, MeasurementKind
from worker.failures import TaskFailedError, TransferGaveUpError
from worker.leases import ActiveLease, CancelToken
from worker.tasks.environment import TaskEnvironment

logger = logging.getLogger(__name__)

ATTEMPTS_PER_SOURCE = 3


def run_archive_clip(
    env: TaskEnvironment, lease: ActiveLease, task: ArchiveClipTask, cancel: CancelToken
) -> None:
    destination = env.storage.clips.archive_path(task.archive_relative_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not _already_archived(destination, task):
        own_copy = env.storage.clips.find(task.job_id, task.sha256)
        if own_copy is not None:
            own_copy.replace(destination)
            logger.info("archived our own clip as %s", task.archive_relative_path)
        else:
            _download(env, task, destination, cancel)
    cancel.raise_if_cancelled()
    confirm = ClipArchivedRequest(lease_id=lease.lease_id, sha256=task.sha256)
    env.report(lambda: env.api.clip_archived(task.job_id, confirm), cancel, "clip-archived")
    logger.info(
        "clip version %d archived at %s", task.version, task.archive_relative_path,
        extra={"job_id": task.job_id},
    )  # fmt: skip


def _already_archived(destination: Path, task: ArchiveClipTask) -> bool:
    """A copy from an earlier run (crash before the confirmation) that checks out."""
    if not destination.exists():
        return False
    if sha256_of_file(destination) == task.sha256:
        return True
    # Something else is at that path (never expected): keep it, under a
    # name that says so, rather than overwrite a clip.
    keep_as = destination.with_name(f"{destination.stem}.conflict-{int(time.time())}.mp4")
    logger.warning("a different file is at %s; moved it to %s", destination, keep_as.name)
    destination.replace(keep_as)
    return False


def _download(
    env: TaskEnvironment, task: ArchiveClipTask, destination: Path, cancel: CancelToken
) -> None:
    if not task.download_urls:
        # The orchestrator thought we keep it, but it is not on our disk.
        raise TaskFailedError("the clip is not on this master's disk and no source was given")
    client = env.transfer_client(cancel)
    for url in task.download_urls:
        absolute_url = env.api.absolute_url(url)
        started_at = time.monotonic()
        try:
            result = download_file(
                client,
                absolute_url,
                destination,
                task.sha256,
                headers=env.api.auth_headers_for(absolute_url),
                max_attempts=ATTEMPTS_PER_SOURCE,
                retry_delay_s=env.settings.transfer_retry_delay_seconds,
                sleep=cancel.sleep,
            )
        except (DownloadFailedError, HashMismatchError, httpx.HTTPError) as error:
            logger.warning("clip source %s failed: %s", absolute_url, error)
            continue
        env.measurements.record(
            MeasurementKind.DOWNLOAD, result.size_bytes - result.resumed_from_byte, started_at
        )
        return
    raise TransferGaveUpError(
        f"could not fetch the clip from any of {len(task.download_urls)} source(s)"
    )
