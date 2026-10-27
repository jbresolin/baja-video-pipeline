"""deliver (studio only): pull a clip from the VPS outbox into the box root.

docs/api.md §5: GET `download_url` (Caddy outbox, with our bearer token),
verify its SHA-256, store it at `clip_relative_path`, rewrite latest.m3u,
then POST /outbox/{id}/ack (which ends the lease).

The download writes the hidden ".<passagem>.mp4.part" beside the final
path and renames it only after the hash matched, so OBS never sees half a
clip, and an interrupted pull resumes where it stopped.

Failures (worker.failures): a clip whose hash is wrong at the outbox or an
unsafe clip path is reported with scope "job"; network trouble is not
reported (the studio is the only one who can deliver, so the lease simply
expires and the task is offered again).
"""

import logging

import httpx

from shared.http_transfer import DownloadFailedError, HashMismatchError, download_file
from shared.models import DeliverTask, OutboxAckRequest
from worker.api_client import ApiError, OrchestratorUnreachableError
from worker.failures import (
    InvalidTaskError,
    SourceHashMismatchError,
    TaskFailedError,
    TransferGaveUpError,
)
from worker.leases import ActiveLease, CancelToken
from worker.studio.library import StudioLibrary, UnsafeClipPathError
from worker.tasks.environment import TaskEnvironment

logger = logging.getLogger(__name__)


def run_deliver(
    env: TaskEnvironment, lease: ActiveLease, task: DeliverTask, cancel: CancelToken
) -> None:
    library = env.studio_library
    if not isinstance(library, StudioLibrary):
        raise TaskFailedError("this worker has no studio clip folder (not a studio?)")
    try:
        destination = library.absolute_path(task.clip_relative_path)
    except UnsafeClipPathError as error:
        raise InvalidTaskError(f"refusing to store the clip: {error}") from error

    url = env.api.absolute_url(task.download_url)
    logger.info("pulling clip from the outbox to %s", task.clip_relative_path)
    try:
        download_file(
            env.transfer_client(cancel),
            url,
            destination,
            task.sha256,
            headers=env.api.auth_headers_for(url),
            retry_delay_s=env.settings.transfer_retry_delay_seconds,
            sleep=cancel.sleep,
        )
    except HashMismatchError as error:
        raise SourceHashMismatchError(f"the outbox clip failed its hash check: {error}") from error
    except (DownloadFailedError, httpx.HTTPError) as error:
        raise TransferGaveUpError(f"outbox download failed: {error}") from error

    job = None
    try:
        job = env.api.get_job(task.job_id)
    except (OrchestratorUnreachableError, ApiError) as error:
        logger.warning("cannot look up the job for the playlist title: %s", error)
    library.record_clip(task.clip_relative_path, job)

    ack = OutboxAckRequest(
        lease_id=lease.lease_id, sha256=task.sha256, relative_path=task.clip_relative_path
    )
    answer = env.report(lambda: env.api.outbox_ack(task.job_id, ack), cancel, "outbox ACK")
    logger.info("outbox ACK sent; job is now %s", answer.state)
