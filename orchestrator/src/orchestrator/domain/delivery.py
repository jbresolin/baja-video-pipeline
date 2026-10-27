"""Studio ACKs (direct push and outbox) and the clip manifest (design §7).

Both ACK routes end with the clip `delivered` and, once the outbox copy (if
any) is deleted, `done`. If deleting fails the job stays `delivered` and
the background outbox cleanup finishes the move.
"""

import logging
import sqlite3
from datetime import UTC, datetime

from orchestrator.context import OrchestratorContext
from orchestrator.db.database import from_db_time, to_db_time
from orchestrator.domain.job_view import require_job_row
from orchestrator.domain.leases import require_active_lease, sweep_expired_leases
from orchestrator.domain.transitions import end_lease, move_job, revoke_job_leases
from orchestrator.errors import ApiError, forbidden, from_illegal_transition, hash_mismatch
from orchestrator.repositories import events as events_repo
from orchestrator.repositories import jobs as jobs_repo
from orchestrator.repositories import leases as leases_repo
from orchestrator.storage import VpsLayout, delete_file_and_partials
from shared.layout import is_safe_relative_path, slugify_prova
from shared.models import (
    DeliveredRequest,
    DeliveryRoute,
    ErrorCode,
    JobState,
    JobStateResponse,
    ManifestEntry,
    ManifestResponse,
    OutboxAckRequest,
    TaskKind,
    WorkerRole,
)
from shared.state_machine import IllegalTransitionError

logger = logging.getLogger(__name__)
ALREADY_DELIVERED_STATES = frozenset({JobState.DELIVERED, JobState.DONE})


def require_studio(worker_row: sqlite3.Row) -> None:
    if worker_row["role"] != WorkerRole.STUDIO:
        raise forbidden("only the studio may acknowledge deliveries")


def finish_delivery(
    connection: sqlite3.Connection, layout: VpsLayout, job_id: str, now: datetime, actor: str
) -> None:
    """delivered → done once the outbox has no copy left (deleting it if needed)."""
    try:
        delete_file_and_partials(layout.outbox_path(job_id))
    except OSError as error:
        logger.warning("outbox copy of %s not deleted yet: %s", job_id, error)
        return
    move_job(
        connection,
        require_job_row(connection, job_id),
        JobState.DONE,
        now,
        actor=actor,
        reason="delivered and outbox copy removed",
    )


def acknowledge_direct_push(
    context: OrchestratorContext, worker_row: sqlite3.Row, job_id: str, request: DeliveredRequest
) -> JobStateResponse:
    require_studio(worker_row)
    if not is_safe_relative_path(request.relative_path):
        raise ApiError(400, ErrorCode.VALIDATION_ERROR, "relative_path is not a safe clip path")
    actor = events_repo.worker_actor(worker_row["id"])
    with context.database.transaction() as connection:
        now = context.clock.now()
        sweep_expired_leases(connection, now)
        job_row = require_job_row(connection, job_id)
        state = JobState(job_row["state"])
        if state in ALREADY_DELIVERED_STATES and job_row["clip_sha256"] == request.sha256:
            return JobStateResponse(job_id=job_id, state=state)  # a repeated ACK
        if state not in (JobState.CLAIMED, JobState.UPLOADED):
            raise from_illegal_transition(IllegalTransitionError(state, JobState.DELIVERED))

        process_leases = leases_repo.list_leases_for_job(connection, job_id, TaskKind.PROCESS)
        renderer = process_leases[0].worker_id if process_leases else job_row["clip_rendered_by"]
        # The ACK is what ends the pushing worker's process lease (docs/api.md §5).
        revoke_job_leases(
            connection,
            job_id,
            now,
            actor=actor,
            reason="studio acknowledged",
            kinds={TaskKind.PROCESS, TaskKind.DELIVER},
        )
        clip_columns = {
            "clip_sha256": request.sha256,
            "clip_size_bytes": request.size_bytes,
            "clip_relative_path": request.relative_path,
            "clip_route": str(DeliveryRoute.DIRECT),
            "clip_rendered_by": renderer or worker_row["id"],
        }
        if state == JobState.CLAIMED:
            move_job(
                connection,
                job_row,
                JobState.UPLOADED,
                now,
                actor=actor,
                reason="clip pushed straight to the studio",
                changes=clip_columns,
            )
            clip_columns = {}
        move_job(
            connection,
            require_job_row(connection, job_id),
            JobState.DELIVERED,
            now,
            actor=actor,
            reason="studio acknowledged a direct push",
            changes={**clip_columns, "clip_delivered_at": to_db_time(now)},
        )
        finish_delivery(connection, context.layout, job_id, now, actor)
        final_state = JobState(require_job_row(connection, job_id)["state"])
    return JobStateResponse(job_id=job_id, state=final_state)


def acknowledge_outbox_pull(
    context: OrchestratorContext, worker_row: sqlite3.Row, job_id: str, request: OutboxAckRequest
) -> JobStateResponse:
    require_studio(worker_row)
    actor = events_repo.worker_actor(worker_row["id"])
    mismatch = None
    with context.database.transaction() as connection:
        now = context.clock.now()
        sweep_expired_leases(connection, now)
        job_row = require_job_row(connection, job_id)
        state = JobState(job_row["state"])
        if state in ALREADY_DELIVERED_STATES and job_row["clip_sha256"] == request.sha256:
            return JobStateResponse(job_id=job_id, state=state)  # a repeated ACK
        lease = require_active_lease(
            connection,
            request.lease_id,
            worker_id=worker_row["id"],
            job_id=job_id,
            kinds={TaskKind.DELIVER},
            now=now,
        )
        if request.sha256 != job_row["clip_sha256"]:
            # Keep the lease: the studio may download again and retry.
            mismatch = hash_mismatch(job_row["clip_sha256"], request.sha256)
        else:
            end_lease(connection, lease, now, actor=actor, reason="studio stored the clip")
            move_job(
                connection,
                job_row,
                JobState.DELIVERED,
                now,
                actor=actor,
                reason="studio pulled the clip from the outbox",
                changes={
                    "clip_delivered_at": to_db_time(now),
                    "clip_relative_path": request.relative_path,
                },
            )
            finish_delivery(connection, context.layout, job_id, now, actor)
            final_state = JobState(require_job_row(connection, job_id)["state"])
    if mismatch is not None:
        raise mismatch
    return JobStateResponse(job_id=job_id, state=final_state)


def build_manifest(
    context: OrchestratorContext, since: datetime | None, prova: str | None
) -> ManifestResponse:
    """Delivered clips, newest first, only the latest per (prova, car, passagem)."""
    try:
        wanted_slug = slugify_prova(prova) if prova else None
    except ValueError as error:
        raise ApiError(400, ErrorCode.VALIDATION_ERROR, str(error)) from error
    if since is not None and since.tzinfo is None:
        since = since.replace(tzinfo=UTC)  # docs/api.md §1: times are UTC
    with context.database.transaction() as connection:
        rows = jobs_repo.list_delivered_job_rows(connection)
    entries = []
    seen_paths = set()
    for row in rows:  # newest delivery first, so the first one per path wins
        if row["clip_relative_path"] in seen_paths:
            continue
        seen_paths.add(row["clip_relative_path"])
        delivered_at = from_db_time(row["clip_delivered_at"])
        if since is not None and delivered_at <= since:
            continue
        if wanted_slug is not None and slugify_prova(row["prova"]) != wanted_slug:
            continue
        entries.append(
            ManifestEntry(
                job_id=row["id"],
                prova=row["prova"],
                car_number=row["car_number"],
                team_name=row["team_name"],
                passagem=row["passagem"],
                relative_path=row["clip_relative_path"],
                sha256=row["clip_sha256"],
                size_bytes=row["clip_size_bytes"],
                delivered_at=delivered_at,
            )
        )
    return ManifestResponse(clips=entries, generated_at=context.clock.now())
