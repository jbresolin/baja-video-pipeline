"""Workers reporting results: holder-confirm and failures (docs/api.md §5)."""

import sqlite3

from orchestrator.context import OrchestratorContext
from orchestrator.db.database import to_db_time
from orchestrator.domain.job_view import require_job_row
from orchestrator.domain.leases import require_active_lease, sweep_expired_leases
from orchestrator.domain.raw_copies import (
    confirmed_holder_count,
    drop_inbox_copy,
    inbox_copy_is_redundant,
)
from orchestrator.domain.transitions import (
    drop_unconfirmed_holder,
    end_lease,
    move_job,
    revoke_job_leases,
)
from orchestrator.errors import ApiError, hash_mismatch
from orchestrator.repositories import events as events_repo
from orchestrator.repositories import jobs as jobs_repo
from orchestrator.repositories import leases as leases_repo
from shared.models import (
    FailedRequest,
    HolderConfirmRequest,
    HolderConfirmResponse,
    JobState,
    JobStateResponse,
    TaskKind,
)
from shared.state_machine import FAILABLE_STATES

# Which job states a failure under each task kind moves to `failed` (design §4).
STATES_FAILED_BY_KIND = {
    TaskKind.DOWNLOAD_RAW: {JobState.RECEIVED, JobState.PROXY_READY},
    TaskKind.MAKE_PROXY: {JobState.RECEIVED, JobState.PROXY_READY},
    TaskKind.PROCESS: {JobState.CLAIMED},
}


def confirm_holder(
    context: OrchestratorContext,
    worker_row: sqlite3.Row,
    job_id: str,
    request: HolderConfirmRequest,
) -> HolderConfirmResponse:
    worker_id = worker_row["id"]
    actor = events_repo.worker_actor(worker_id)
    mismatch: ApiError | None = None
    with context.database.transaction() as connection:
        now = context.clock.now()
        sweep_expired_leases(connection, now)
        job_row = require_job_row(connection, job_id)
        lease = require_active_lease(
            connection,
            request.lease_id,
            worker_id=worker_id,
            job_id=job_id,
            kinds={TaskKind.DOWNLOAD_RAW},
            now=now,
        )
        end_lease(connection, lease, now, actor=actor, reason="holder confirmed or rejected")

        if request.sha256 != job_row["sha256"] or request.size_bytes != job_row["size_bytes"]:
            # The worker's copy is wrong: it is no holder and must delete it.
            leases_repo.remove_holder(connection, job_id, worker_id)
            events_repo.record_event(
                connection,
                now,
                actor=actor,
                kind="holder_rejected",
                job_id=job_id,
                worker_id=worker_id,
                reason=f"reported sha256 {request.sha256}",
            )
            # Raised after the commit, so the cleanup above is kept.
            mismatch = hash_mismatch(job_row["sha256"], request.sha256)
        else:
            raw_deleted = _record_confirmation(context, connection, job_row, worker_id, request)
            count = confirmed_holder_count(connection, job_id)
    if mismatch is not None:
        raise mismatch
    return HolderConfirmResponse(confirmed_holder_count=count, raw_deleted_from_vps=raw_deleted)


def _record_confirmation(
    context: OrchestratorContext,
    connection: sqlite3.Connection,
    job_row: sqlite3.Row,
    worker_id: str,
    request: HolderConfirmRequest,
) -> bool:
    now = context.clock.now()
    job_id = job_row["id"]
    leases_repo.add_assigned_holder(connection, job_id, worker_id, now)
    leases_repo.confirm_holder(connection, job_id, worker_id, now)
    events_repo.record_event(
        connection,
        now,
        actor=events_repo.worker_actor(worker_id),
        kind="holder_confirmed",
        job_id=job_id,
        worker_id=worker_id,
    )

    # The first holder to probe the raw fills in what ffprobe found.
    probe_changes = {}
    if job_row["duration_s"] is None and request.duration_s is not None:
        probe_changes["duration_s"] = request.duration_s
    if job_row["has_audio"] is None and request.has_audio is not None:
        probe_changes["has_audio"] = int(request.has_audio)
    if probe_changes:
        jobs_repo.update_job(connection, job_id, now, probe_changes)

    if inbox_copy_is_redundant(connection, job_row):
        return drop_inbox_copy(
            connection,
            context.layout,
            job_row,
            now,
            actor="system",
            reason="two holders confirmed the raw",
        )
    return False


def report_failure(
    context: OrchestratorContext, worker_row: sqlite3.Row, job_id: str, request: FailedRequest
) -> JobStateResponse:
    worker_id = worker_row["id"]
    actor = events_repo.worker_actor(worker_id)
    with context.database.transaction() as connection:
        now = context.clock.now()
        sweep_expired_leases(connection, now)
        job_row = require_job_row(connection, job_id)
        lease = require_active_lease(
            connection, request.lease_id, worker_id=worker_id, job_id=job_id, now=now
        )
        end_lease(connection, lease, now, actor=actor, reason="worker reported a failure")
        if lease.task_kind == TaskKind.DOWNLOAD_RAW:
            drop_unconfirmed_holder(connection, job_id, worker_id)

        state = JobState(job_row["state"])
        failure_columns = {
            "failure_worker_id": worker_id,
            "failure_task_kind": str(lease.task_kind),
            "failure_message": request.message,
            "failure_exit_code": request.exit_code,
            "failure_at": to_db_time(now),
        }
        reason = f"{lease.task_kind} failed: {request.message[:200]}"
        if state in FAILABLE_STATES and state in STATES_FAILED_BY_KIND.get(lease.task_kind, ()):
            # The job waits for a human: retry (back to `state`) or discard.
            revoke_job_leases(
                connection, job_id, now, actor=actor, reason="job failed on another worker"
            )
            move_job(
                connection,
                job_row,
                JobState.FAILED,
                now,
                actor=actor,
                reason=reason,
                changes={**failure_columns, "failed_from": str(state), "waiting_reason": None},
            )
            new_state = JobState.FAILED
        else:
            # The job carries on; only this worker stops trying this task here.
            leases_repo.add_task_exclusion(connection, job_id, worker_id, lease.task_kind, now)
            waiting = f"{lease.task_kind} failed on {worker_row['name']}: {request.message[:200]}"
            jobs_repo.update_job(
                connection, job_id, now, {**failure_columns, "waiting_reason": waiting}
            )
            events_repo.record_event(
                connection,
                now,
                actor=actor,
                kind="task_failed",
                job_id=job_id,
                worker_id=worker_id,
                reason=reason,
            )
            new_state = state
    return JobStateResponse(job_id=job_id, state=new_state)
