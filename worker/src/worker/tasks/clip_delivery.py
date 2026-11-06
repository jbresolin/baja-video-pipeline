"""Deliver a rendered clip the way the offer says: studio push or VPS outbox.

docs/api.md §5 "Delivery decision" and "Direct push and the process lease",
design §7 and §17. The orchestrator decides the route and puts it in the
offer as `task.delivery`; the worker never works it out itself.

  `outbox`: upload straight to the outbox (POST …/clip under the lease).
  `direct`: push the clip to `delivery.studio_url` (chunked, with
     Clip-Relative-Path; no bearer token, the studio is not an origin we
     trust with it). Then keep the lease listed in heartbeats: the studio
     ACKs to the orchestrator, which ends the lease, and our heartbeat
     reports it `lost`, which here means "finished". If the push fails, or
     the lease is still being renewed 60 s after the push completed,
     upload to the outbox instead.

Fast fallback (LIVESTACK-41 item 13): before pushing, the studio must
answer GET /health within 3 s, else the clip goes straight to the outbox
and the studio is remembered as unreachable for 5 minutes
(TaskEnvironment.studio_reachability). And after a push whose ACK did not
come within 60 s, the job is looked up first: if the studio's ACK reached
the orchestrator meanwhile (job delivered or done with this clip), there is
nothing left to do, and uploading to the outbox would only make the studio
receive the same clip twice.
"""

import logging
import time
from pathlib import Path

import httpx

from shared.hashing import sha256_of_file
from shared.http_transfer import HashMismatchError, UploadFailedError, upload_file_in_chunks
from shared.models import DeliveryRoute, JobState, MeasurementKind, ProcessTask
from shared.protocol import CLIP_RELATIVE_PATH_HEADER
from worker.api_client import ApiError, OrchestratorUnreachableError
from worker.failures import TransferGaveUpError
from worker.leases import REASON_LEASE_LOST, ActiveLease, CancelToken, TaskCancelledError
from worker.tasks.environment import TaskEnvironment

logger = logging.getLogger(__name__)


def deliver_clip(
    env: TaskEnvironment,
    lease: ActiveLease,
    task: ProcessTask,
    clip_path: Path,
    cancel: CancelToken,
    *,
    clip_sha256: str | None = None,
) -> str:
    """Deliver the clip; returns its SHA-256 (the worker then keeps it under that name).

    `clip_sha256` saves hashing the file again when the caller already did.
    """
    clip_sha256 = clip_sha256 or sha256_of_file(clip_path)
    studio_url = direct_push_url(task)
    if studio_url is not None and not env.studio_reachability.reachable(
        studio_url, env.transfer_transport
    ):
        logger.warning("the studio does not answer at %s; using the outbox", studio_url)
        studio_url = None
    if studio_url is not None and _push_to_studio(
        env, task, clip_path, clip_sha256, studio_url, cancel
    ):
        if _studio_ack_ended_lease(env, lease, cancel):
            logger.info("studio acknowledged the clip; delivery finished")
            return clip_sha256
        if _orchestrator_already_has_the_ack(env, task, clip_sha256):
            logger.info("the studio's ACK arrived meanwhile; delivery finished")
            return clip_sha256
        logger.warning(
            "lease still renewed %.0f s after the push; uploading to the outbox as well",
            env.settings.push_ack_wait_seconds,
        )
    _upload_to_outbox(env, lease, task, clip_path, clip_sha256, cancel)
    return clip_sha256


def _orchestrator_already_has_the_ack(
    env: TaskEnvironment, task: ProcessTask, clip_sha256: str
) -> bool:
    """True when the job is delivered/done with exactly this clip."""
    try:
        job = env.api.get_job(task.job_id)
    except (OrchestratorUnreachableError, ApiError) as error:
        logger.warning("cannot check the job before the outbox fallback: %s", error)
        return False
    delivered = job.state in (JobState.DELIVERED, JobState.DONE)
    return delivered and job.clip is not None and job.clip.sha256 == clip_sha256


def direct_push_url(task: ProcessTask) -> str | None:
    """The studio's receive URL when the offer says "direct", else None (= outbox)."""
    plan = task.delivery
    if plan.mode != DeliveryRoute.DIRECT:
        logger.info("the orchestrator says: deliver through the outbox")
        return None
    if not plan.studio_url:
        # Never expected (the contract pairs "direct" with a URL); the
        # outbox always works, so use it rather than fail.
        logger.warning("delivery is 'direct' but has no studio_url; using the outbox")
        return None
    return plan.studio_url


def _push_to_studio(
    env: TaskEnvironment,
    task: ProcessTask,
    clip_path: Path,
    clip_sha256: str,
    url: str,
    cancel: CancelToken,
) -> bool:
    """True when the studio stored and verified the whole clip."""
    logger.info("pushing clip to the studio at %s as %s", url, task.clip_relative_path)
    started_at = time.monotonic()
    try:
        result = upload_file_in_chunks(
            env.transfer_client(cancel),
            url,
            clip_path,
            sha256=clip_sha256,
            # auth_headers_for(url) is empty for a studio URL; asking anyway
            # keeps the one token rule (worker.token_origins) in charge.
            headers={
                **env.api.auth_headers_for(url),
                CLIP_RELATIVE_PATH_HEADER: task.clip_relative_path,
            },
            # The outbox is a fine fallback, so do not insist for long.
            max_attempts=3,
            chunk_size=env.settings.upload_chunk_bytes,
            retry_delay_s=env.settings.transfer_retry_delay_seconds,
            sleep=cancel.sleep,
        )
    except (UploadFailedError, HashMismatchError, httpx.HTTPError) as error:
        logger.warning("direct push failed (%s); falling back to the outbox", error)
        if isinstance(error, httpx.TransportError):
            env.studio_reachability.mark_unreachable(url)
        return False
    env.measurements.record(MeasurementKind.UPLOAD, result.size_bytes, started_at)
    return True


def _studio_ack_ended_lease(env: TaskEnvironment, lease: ActiveLease, cancel: CancelToken) -> bool:
    """Wait for the heartbeat to report our lease `lost` (= the studio ACKed)."""
    deadline = time.monotonic() + env.settings.push_ack_wait_seconds
    while time.monotonic() < deadline:
        if lease.lost.wait(timeout=min(1.0, max(0.0, deadline - time.monotonic()))):
            return True
        if cancel.shutting_down:
            cancel.raise_if_cancelled()
    return lease.lost.is_set()


def _upload_to_outbox(
    env: TaskEnvironment,
    lease: ActiveLease,
    task: ProcessTask,
    clip_path: Path,
    clip_sha256: str,
    cancel: CancelToken,
) -> None:
    cancel.raise_if_cancelled()
    logger.info("uploading clip to the outbox")
    outbox_url = env.api.absolute_url(task.outbox_upload_url)
    started_at = time.monotonic()
    try:
        result = upload_file_in_chunks(
            env.transfer_client(cancel),
            outbox_url,
            clip_path,
            sha256=clip_sha256,
            headers=env.api.upload_headers(outbox_url, lease.lease_id),
            chunk_size=env.settings.upload_chunk_bytes,
            retry_delay_s=env.settings.transfer_retry_delay_seconds,
            sleep=cancel.sleep,
        )
    except UploadFailedError as error:
        if error.error_code == "lease_lost":
            raise TaskCancelledError(REASON_LEASE_LOST) from error
        raise TransferGaveUpError(f"outbox upload failed: {error}") from error
    except HashMismatchError as error:
        raise TransferGaveUpError("the clip arrived corrupted at the outbox") from error
    env.measurements.record(MeasurementKind.UPLOAD, result.size_bytes, started_at)
    logger.info("clip uploaded to the outbox (%d bytes)", result.size_bytes)
