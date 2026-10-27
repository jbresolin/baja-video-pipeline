"""Deliver a rendered clip: push to the studio, fall back to the VPS outbox.

docs/api.md §5 "Direct push and the process lease" and design §7:

 1. If GET /workers lists an online studio with a receiver port, push the
    clip there (chunked, with Clip-Relative-Path). The offer has no "push
    or not" flag, so an online studio is what decides.
 2. After the studio answered `complete`, keep the lease listed in
    heartbeats. The studio ACKs to the orchestrator, which ends the lease:
    our heartbeat then reports it `lost`, which here means "finished".
 3. If the push failed, there is no studio, or the lease is still being
    renewed 60 s after the push completed, upload to the outbox
    (POST …/clip under the lease).
"""

import logging
import time
from pathlib import Path

import httpx

from shared.hashing import sha256_of_file
from shared.http_transfer import HashMismatchError, UploadFailedError, upload_file_in_chunks
from shared.models import MeasurementKind, ProcessTask, WorkerRole
from shared.protocol import CLIP_RELATIVE_PATH_HEADER
from worker.api_client import ApiError, OrchestratorUnreachableError
from worker.leases import REASON_LEASE_LOST, ActiveLease, CancelToken, TaskCancelledError
from worker.tasks.environment import TaskAbandonedError, TaskEnvironment

logger = logging.getLogger(__name__)


def deliver_clip(
    env: TaskEnvironment,
    lease: ActiveLease,
    task: ProcessTask,
    clip_path: Path,
    cancel: CancelToken,
) -> None:
    clip_sha256 = sha256_of_file(clip_path)
    receiver_url = find_studio_receiver_url(env, task.job_id)
    if receiver_url is not None and _push_to_studio(
        env, task, clip_path, clip_sha256, receiver_url, cancel
    ):
        if _studio_ack_ended_lease(env, lease, cancel):
            logger.info("studio acknowledged the clip; delivery finished")
            return
        logger.warning(
            "lease still renewed %.0f s after the push; uploading to the outbox as well",
            env.settings.push_ack_wait_seconds,
        )
    _upload_to_outbox(env, lease, task, clip_path, clip_sha256, cancel)


def find_studio_receiver_url(env: TaskEnvironment, job_id: str) -> str | None:
    """Base URL of an online studio's receiver, e.g. "http://100.64.0.9:8720"."""
    try:
        peers = env.api.peers().workers
    except (OrchestratorUnreachableError, ApiError) as error:
        logger.warning("cannot list peers (%s); using the outbox", error)
        return None
    for peer in peers:
        is_online_studio = peer.role == WorkerRole.STUDIO and peer.online
        if is_online_studio and peer.tailnet_address and peer.receiver_port:
            return f"http://{peer.tailnet_address}:{peer.receiver_port}"
    logger.info("no studio online; using the outbox")
    return None


def _push_to_studio(
    env: TaskEnvironment,
    task: ProcessTask,
    clip_path: Path,
    clip_sha256: str,
    receiver_url: str,
    cancel: CancelToken,
) -> bool:
    """True when the studio stored and verified the whole clip."""
    url = f"{receiver_url}/receive/{task.job_id}"
    logger.info("pushing clip to the studio at %s as %s", url, task.clip_relative_path)
    started_at = time.monotonic()
    try:
        result = upload_file_in_chunks(
            env.transfer_client(cancel),
            url,
            clip_path,
            sha256=clip_sha256,
            headers={CLIP_RELATIVE_PATH_HEADER: task.clip_relative_path},
            # The outbox is a fine fallback, so do not insist for long.
            max_attempts=3,
            chunk_size=env.settings.upload_chunk_bytes,
            sleep=cancel.sleep,
        )
    except (UploadFailedError, HashMismatchError, httpx.HTTPError) as error:
        logger.warning("direct push failed (%s); falling back to the outbox", error)
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
    started_at = time.monotonic()
    try:
        result = upload_file_in_chunks(
            env.transfer_client(cancel),
            env.api.absolute_url(task.outbox_upload_url),
            clip_path,
            sha256=clip_sha256,
            headers=env.api.upload_headers(lease.lease_id),
            chunk_size=env.settings.upload_chunk_bytes,
            sleep=cancel.sleep,
        )
    except UploadFailedError as error:
        if error.error_code == "lease_lost":
            raise TaskCancelledError(REASON_LEASE_LOST) from error
        raise TaskAbandonedError(f"outbox upload failed: {error}") from error
    except HashMismatchError as error:
        raise TaskAbandonedError("clip arrived corrupted at the outbox; will retry") from error
    env.measurements.record(MeasurementKind.UPLOAD, result.size_bytes, started_at)
    logger.info("clip uploaded to the outbox (%d bytes)", result.size_bytes)
