"""deliver (studio only): pull a clip from the VPS outbox into the box root.

docs/api.md §5: GET `download_url` (Caddy outbox, with our bearer token),
verify its SHA-256, store it at `clip_relative_path`, rewrite latest.m3u,
then POST /outbox/{id}/ack (which ends the lease).

The download goes to a hidden staging file beside the final path and is
moved into place only after the hash matched, so OBS never sees half a
clip, and an interrupted pull resumes where it stopped. Moving it into
place is StudioLibrary.place_clip (LIVESTACK-41 item 13): if the folder
already holds this exact clip (a second delivery: the direct push got
there first, or the ACK was lost), nothing is downloaded at all and the
ACK is sent; a clip on air that stays locked is never overwritten, the
new version goes beside it (…-v2.mp4) and that name is ACKed.

Failures (worker.failures): a clip whose hash is wrong at the outbox or an
unsafe clip path is reported with scope "job"; network trouble is not
reported (the studio is the only one who can deliver, so the lease simply
expires and the task is offered again).
"""

import logging
from pathlib import Path

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

STAGING_SUFFIX = ".incoming"


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

    stored_as = library.find_same_content(task.clip_relative_path, task.sha256)
    if stored_as is not None:
        logger.info("the studio already has this clip as %s; not downloading it again", stored_as)
    else:
        stored_as = _pull_and_place(env, library, task, destination, cancel)

    job = None
    try:
        job = env.api.get_job(task.job_id)
    except (OrchestratorUnreachableError, ApiError) as error:
        logger.warning("cannot look up the job for the playlist title: %s", error)
    library.record_clip(stored_as, job)

    ack = OutboxAckRequest(lease_id=lease.lease_id, sha256=task.sha256, relative_path=stored_as)
    answer = env.report(lambda: env.api.outbox_ack(task.job_id, ack), cancel, "outbox ACK")
    logger.info("outbox ACK sent; job is now %s", answer.state)


def _pull_and_place(
    env: TaskEnvironment,
    library: StudioLibrary,
    task: DeliverTask,
    destination: Path,
    cancel: CancelToken,
) -> str:
    """Download into a hidden staging file, then move it into place; returns the name used."""
    # ".enduro-042-p3.mp4.incoming": hidden from OBS (and from the cleanup
    # of superseded versions); download_file resumes it after a restart.
    staging = destination.with_name(f".{destination.name}{STAGING_SUFFIX}")
    url = env.api.absolute_url(task.download_url)
    logger.info("pulling clip from the outbox to %s", task.clip_relative_path)
    try:
        download_file(
            env.transfer_client(cancel),
            url,
            staging,
            task.sha256,
            headers=env.api.auth_headers_for(url),
            retry_delay_s=env.settings.transfer_retry_delay_seconds,
            sleep=cancel.sleep,
        )
    except HashMismatchError as error:
        raise SourceHashMismatchError(f"the outbox clip failed its hash check: {error}") from error
    except (DownloadFailedError, httpx.HTTPError) as error:
        raise TransferGaveUpError(f"outbox download failed: {error}") from error
    return library.place_clip(staging, task.clip_relative_path, task.sha256)
