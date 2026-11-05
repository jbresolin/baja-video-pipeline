"""Clip versions, the master's archive confirmations, and when outbox copies may go.

Design §11 as amended (LIVESTACK-38 item 7): processed clips are the
archive. Every clip that reaches the outbox or the studio is recorded as a
version (clip_versions); the master copies each one (archive_clip, see
archive_rules.py) and confirms it by sha256 here. Workers keep the clips
they render and may evict one only after the master archived it.

Outbox copies are deleted only when the studio has ACKed *and* the master
has archived that clip, or, if the master stays offline, once
outbox_keep_unarchived_hours (Settings page, default 72 h) have passed since the ACK,
so an offline master cannot lose a clip the studio already took.
"""

import sqlite3
from datetime import datetime

from orchestrator.context import OrchestratorContext
from orchestrator.db.database import from_db_time
from orchestrator.domain.job_view import require_job_row
from orchestrator.domain.leases import require_active_lease, sweep_expired_leases
from orchestrator.domain.transitions import end_lease
from orchestrator.errors import forbidden, hash_mismatch
from orchestrator.repositories import clips as clips_repo
from orchestrator.repositories import events as events_repo
from orchestrator.repositories import workers as workers_repo
from shared.models import (
    ClipArchivedRequest,
    ClipArchiveProgress,
    HeldClip,
    JobState,
    JobStateResponse,
    TaskKind,
    WorkerRole,
)

STATES_WITH_DELIVERED_CLIP = frozenset({JobState.DELIVERED, JobState.DONE})


def record_clip_version(
    connection: sqlite3.Connection,
    job_id: str,
    *,
    sha256: str,
    size_bytes: int,
    relative_path: str,
    rendered_by: str | None,
    now: datetime,
) -> int:
    """Remember a finished clip as the job's next version; same sha256 = same version."""
    existing = clips_repo.get_version_by_sha(connection, job_id, sha256)
    if existing is not None:
        return existing["version"]
    return clips_repo.insert_version(
        connection, job_id, sha256, size_bytes, relative_path, rendered_by, now
    )


def confirm_clip_archived(
    context: OrchestratorContext, worker_row: sqlite3.Row, job_id: str, request: ClipArchivedRequest
) -> JobStateResponse:
    """POST /jobs/{id}/clip-archived: mark the version archived and end the lease."""
    if worker_row["role"] != WorkerRole.MASTER:
        raise forbidden("only the master archives clips")
    with context.database.transaction() as connection:
        now = context.clock.now()
        sweep_expired_leases(connection, now)
        job_row = require_job_row(connection, job_id)
        lease = require_active_lease(
            connection,
            request.lease_id,
            worker_id=worker_row["id"],
            job_id=job_id,
            kinds={TaskKind.ARCHIVE_CLIP},
            now=now,
        )
        version_row = clips_repo.get_version_by_sha(connection, job_id, request.sha256)
        if version_row is None:
            # Not a clip of this job: the copy is wrong. Keep the lease so
            # the master can fetch again from another source.
            raise hash_mismatch("a clip version of this job", request.sha256)
        clips_repo.mark_archived(connection, job_id, request.sha256, now)
        actor = events_repo.worker_actor(worker_row["id"])
        end_lease(connection, lease, now, actor=actor, reason="clip archived")
        events_repo.record_event(
            connection,
            now,
            actor=actor,
            kind="clip_archived",
            job_id=job_id,
            worker_id=worker_row["id"],
            reason=f"version {version_row['version']} ({request.sha256[:12]}…)",
        )
        return JobStateResponse(job_id=job_id, state=JobState(job_row["state"]))


def outbox_copy_still_needed(
    connection: sqlite3.Connection, job_row: sqlite3.Row, now: datetime, keep_unarchived
) -> bool:
    """True while the outbox file must stay: not yet ACKed, or ACKed but not archived (72 h)."""
    state = JobState(job_row["state"])
    if state in (JobState.CLAIMED, JobState.UPLOADED):
        return True  # the studio has not taken it yet
    if state not in STATES_WITH_DELIVERED_CLIP or job_row["clip_sha256"] is None:
        return False
    version_row = clips_repo.get_version_by_sha(connection, job_row["id"], job_row["clip_sha256"])
    if version_row is None or version_row["archived_at"] is not None:
        return False
    delivered_at = from_db_time(job_row["clip_delivered_at"])
    return delivered_at is None or now - delivered_at < keep_unarchived


# --- Heartbeats and the fleet view ---


def store_held_clips(connection: sqlite3.Connection, worker_id: str, held: list[HeldClip]) -> None:
    clips_repo.replace_worker_clips(connection, worker_id, [(c.job_id, c.sha256) for c in held])


def archived_among(connection: sqlite3.Connection, held: list[HeldClip]) -> list[str]:
    """The sha256 of each held clip the master has archived: the worker may evict those."""
    return sorted({c.sha256 for c in held if clips_repo.is_sha_archived(connection, c.sha256)})


def worker_clip_counts(connection: sqlite3.Connection, worker_id: str) -> tuple[int, int]:
    """(clips the worker keeps, how many of those the master archived)."""
    shas = clips_repo.list_worker_clip_shas(connection, worker_id)
    archived = sum(1 for sha in shas if clips_repo.is_sha_archived(connection, sha))
    return len(shas), archived


def archive_progress(connection: sqlite3.Connection) -> ClipArchiveProgress:
    archived, total = clips_repo.count_versions(connection)
    return ClipArchiveProgress(archived=archived, total=total)


def forget_worker_clips_on_revoke(connection: sqlite3.Connection, worker_id: str) -> None:
    """On worker revocation (admin Workers page): its clips are no longer a source."""
    if workers_repo.get_worker_row(connection, worker_id) is not None:
        clips_repo.replace_worker_clips(connection, worker_id, [])
