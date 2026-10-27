"""Proxy, thumbnail and clip uploads from workers (docs/api.md §6.2).

Chunks are assembled by shared's ChunkedUploadReceiver, exactly like the
studio receiver does. Bytes are written outside the DB lock (a chunk can be
8 MiB); a per-file lock keeps two chunks of the same file from
interleaving. Only the completing chunk changes the job.
"""

import sqlite3
from collections.abc import Callable, Mapping
from pathlib import Path

from orchestrator.context import OrchestratorContext
from orchestrator.domain.job_view import require_job_row
from orchestrator.domain.leases import require_active_lease, sweep_expired_leases
from orchestrator.domain.transitions import end_lease, move_job
from orchestrator.errors import ApiError
from orchestrator.repositories import events as events_repo
from orchestrator.repositories import jobs as jobs_repo
from shared.http_transfer import partial_path_for
from shared.layout import clip_relative_path
from shared.models import ChunkAccepted, DeliveryRoute, ErrorCode, JobState, TaskKind
from shared.upload_receiver import (
    ChunkedUploadReceiver,
    InvalidUploadHeadersError,
    UploadHashMismatchError,
    UploadHeaders,
    UploadOffsetMismatchError,
    UploadTooLargeError,
    read_upload_headers,
)

# Generous ceilings: a 480p proxy of a 2 GB raw is ~100 MB; a clip is a
# trimmed re-encode of at most the whole raw.
MAX_PROXY_BYTES = 1_000_000_000
MAX_CLIP_BYTES = 4_000_000_000
MAX_THUMBNAIL_BYTES = 5_000_000
JPEG_MAGIC = b"\xff\xd8\xff"


def parse_upload_headers(headers: Mapping[str, str]) -> UploadHeaders:
    try:
        return read_upload_headers(headers)
    except InvalidUploadHeadersError as error:
        raise ApiError(400, ErrorCode.VALIDATION_ERROR, str(error)) from error


def _check_lease(
    context: OrchestratorContext,
    worker_row: sqlite3.Row,
    job_id: str,
    lease_id: str,
    kind: TaskKind,
) -> sqlite3.Row:
    with context.database.transaction() as connection:
        now = context.clock.now()
        sweep_expired_leases(connection, now)
        job_row = require_job_row(connection, job_id)
        require_active_lease(
            connection, lease_id, worker_id=worker_row["id"], job_id=job_id, kinds={kind}, now=now
        )
        return job_row


def _receive_chunk(
    context: OrchestratorContext, path: Path, max_bytes: int, upload: UploadHeaders, chunk: bytes
) -> ChunkAccepted:
    receiver = ChunkedUploadReceiver(path, max_bytes=max_bytes)
    with context.upload_locks.lock_for(str(path)):
        try:
            return receiver.accept_chunk(upload, chunk)
        except UploadOffsetMismatchError as error:
            raise ApiError(
                409,
                ErrorCode.UPLOAD_OFFSET_MISMATCH,
                str(error),
                {"expected_offset": error.expected_offset},
            ) from error
        except UploadHashMismatchError as error:
            raise ApiError(422, ErrorCode.HASH_MISMATCH, str(error)) from error
        except UploadTooLargeError as error:
            raise ApiError(413, ErrorCode.UPLOAD_TOO_LARGE, str(error)) from error


def _finish_under_lease(
    context: OrchestratorContext,
    worker_row: sqlite3.Row,
    job_id: str,
    lease_id: str,
    kind: TaskKind,
    apply: Callable[[sqlite3.Connection, sqlite3.Row], None],
) -> None:
    """Re-check the lease, apply the completed upload to the job, end the lease."""
    with context.database.transaction() as connection:
        now = context.clock.now()
        job_row = require_job_row(connection, job_id)
        lease = require_active_lease(
            connection, lease_id, worker_id=worker_row["id"], job_id=job_id, kinds={kind}, now=now
        )
        apply(connection, job_row)
        end_lease(
            connection,
            lease,
            now,
            actor=events_repo.worker_actor(worker_row["id"]),
            reason=f"{kind} upload complete",
        )


def accept_proxy_chunk(
    context: OrchestratorContext,
    worker_row: sqlite3.Row,
    job_id: str,
    lease_id: str,
    headers: Mapping[str, str],
    chunk: bytes,
    frame_rate: float | None = None,
) -> ChunkAccepted:
    """`frame_rate` (Frame-Rate header) is stored on the job when the upload completes."""
    upload = parse_upload_headers(headers)
    _check_lease(context, worker_row, job_id, lease_id, TaskKind.MAKE_PROXY)
    accepted = _receive_chunk(
        context, context.layout.proxy_path(job_id), MAX_PROXY_BYTES, upload, chunk
    )
    if not accepted.complete:
        return accepted

    def proxy_is_in_place(connection: sqlite3.Connection, job_row: sqlite3.Row) -> None:
        now = context.clock.now()
        actor = events_repo.worker_actor(worker_row["id"])
        changes = {"proxy_available": 1, "proxy_requested": 0}
        if frame_rate is not None:
            changes["frame_rate"] = frame_rate
        if job_row["state"] != JobState.RECEIVED:
            # A re-rendered proxy (regenerate-proxy): no state change.
            jobs_repo.update_job(connection, job_id, now, changes)
            return
        move_job(
            connection,
            job_row,
            JobState.PROXY_READY,
            now,
            actor=actor,
            reason="proxy uploaded",
            changes=changes,
        )
        job_row = require_job_row(connection, job_id)
        move_job(
            connection,
            job_row,
            JobState.AWAITING_REVIEW,
            now,
            actor="system",
            reason="proxy ready: queued for review",
            changes={"waiting_reason": None},
        )

    _finish_under_lease(
        context, worker_row, job_id, lease_id, TaskKind.MAKE_PROXY, proxy_is_in_place
    )
    return accepted


def store_thumbnail(
    context: OrchestratorContext,
    worker_row: sqlite3.Row,
    job_id: str,
    lease_id: str,
    jpeg: bytes,
) -> None:
    _check_lease(context, worker_row, job_id, lease_id, TaskKind.MAKE_PROXY)
    if len(jpeg) > MAX_THUMBNAIL_BYTES:
        raise ApiError(413, ErrorCode.UPLOAD_TOO_LARGE, "thumbnail is over 5 MB")
    if not jpeg.startswith(JPEG_MAGIC):
        raise ApiError(400, ErrorCode.VALIDATION_ERROR, "thumbnail must be a JPEG")
    final_path = context.layout.thumbnail_path(job_id)
    partial = partial_path_for(final_path)
    final_path.parent.mkdir(parents=True, exist_ok=True)
    with context.upload_locks.lock_for(str(final_path)):
        partial.write_bytes(jpeg)
        partial.replace(final_path)
    with context.database.transaction() as connection:
        require_job_row(connection, job_id)
        jobs_repo.update_job(connection, job_id, context.clock.now(), {"thumbnail_available": 1})


def accept_clip_chunk(
    context: OrchestratorContext,
    worker_row: sqlite3.Row,
    job_id: str,
    lease_id: str,
    headers: Mapping[str, str],
    chunk: bytes,
) -> ChunkAccepted:
    upload = parse_upload_headers(headers)
    job_row = _check_lease(context, worker_row, job_id, lease_id, TaskKind.PROCESS)
    if job_row["state"] != JobState.CLAIMED:
        raise ApiError(
            409,
            ErrorCode.ILLEGAL_TRANSITION,
            "clip uploads need a claimed job",
            {"current": job_row["state"], "requested": str(JobState.UPLOADED)},
        )
    accepted = _receive_chunk(
        context, context.layout.outbox_path(job_id), MAX_CLIP_BYTES, upload, chunk
    )
    if not accepted.complete:
        return accepted

    def clip_is_in_outbox(connection: sqlite3.Connection, job_row: sqlite3.Row) -> None:
        move_job(
            connection,
            job_row,
            JobState.UPLOADED,
            context.clock.now(),
            actor=events_repo.worker_actor(worker_row["id"]),
            reason="clip uploaded to outbox",
            changes={
                "clip_sha256": upload.sha256,
                "clip_size_bytes": upload.total_length,
                "clip_relative_path": clip_relative_path(
                    job_row["prova"], job_row["car_number"], job_row["passagem"]
                ),
                "clip_route": str(DeliveryRoute.OUTBOX),
                "clip_rendered_by": worker_row["id"],
                "clip_delivered_at": None,
            },
        )

    _finish_under_lease(context, worker_row, job_id, lease_id, TaskKind.PROCESS, clip_is_in_outbox)
    return accepted
