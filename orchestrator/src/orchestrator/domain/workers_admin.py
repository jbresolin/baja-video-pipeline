"""Admin Enrollment and Workers pages: list and revoke tokens, revoke workers.

Revoking a worker makes its bearer token stop working at once (401 on
every call), ends its leases so their tasks go to other workers, and
drops it as a holder of every raw, so the orchestrator stops counting on
copies it can no longer reach. Removing the device from the tailnet stays
a manual step in the Tailscale admin console (the page says so).
"""

from datetime import datetime

from orchestrator.context import OrchestratorContext
from orchestrator.db.database import from_db_time
from orchestrator.domain.audit import AdminActor, record_admin_change
from orchestrator.domain.enrollment import token_id_for
from orchestrator.domain.transitions import end_lease
from orchestrator.errors import ApiError, not_found
from orchestrator.repositories import events as events_repo
from orchestrator.repositories import leases as leases_repo
from orchestrator.repositories import workers as workers_repo
from shared.models import (
    EnrollmentTokenInfo,
    EnrollmentTokensResponse,
    EnrollmentTokenStatus,
    ErrorCode,
    Worker,
    WorkerRole,
)


def _token_status(row, now: datetime) -> EnrollmentTokenStatus:
    if row["used_at"] is not None:
        return EnrollmentTokenStatus.USED
    if row["revoked_at"] is not None:
        return EnrollmentTokenStatus.REVOKED
    if from_db_time(row["expires_at"]) <= now:
        return EnrollmentTokenStatus.EXPIRED
    return EnrollmentTokenStatus.UNUSED


def list_enrollment_tokens(context: OrchestratorContext) -> EnrollmentTokensResponse:
    now = context.clock.now()
    with context.database.transaction() as connection:
        rows = workers_repo.list_enrollment_token_rows(connection)
    return EnrollmentTokensResponse(
        tokens=[
            EnrollmentTokenInfo(
                token_id=token_id_for(row["token_sha256"]),
                volunteer_name=row["volunteer_name"],
                role=WorkerRole(row["role"]),
                status=_token_status(row, now),
                created_at=from_db_time(row["created_at"]),
                expires_at=from_db_time(row["expires_at"]),
                used_at=from_db_time(row["used_at"]),
                used_by_worker_id=row["used_by_worker_id"],
                used_by_worker_name=row["worker_name"],
                revoked_at=from_db_time(row["revoked_at"]),
            )
            for row in rows
        ]
    )


def revoke_enrollment_token(
    context: OrchestratorContext, actor: AdminActor, token_id: str
) -> EnrollmentTokenInfo:
    """Revoke a token nobody used yet (expired ones too). 409 for a used token."""
    now = context.clock.now()
    with context.database.transaction() as connection:
        rows = workers_repo.find_enrollment_token_rows_by_prefix(connection, token_id)
        if len(rows) != 1 or len(token_id) < 8:
            raise not_found("enrollment token")
        row = rows[0]
        if row["used_at"] is not None:
            raise ApiError(
                409,
                ErrorCode.CONFLICT,
                "this token was already used; revoke the worker on the Workers page instead",
            )
        if row["revoked_at"] is None:
            workers_repo.revoke_enrollment_token(connection, row["token_sha256"], now)
            record_admin_change(
                connection,
                now,
                actor,
                "enrollment_token_revoked",
                f"{row['role']} token for {row['volunteer_name']} revoked",
                before={"token_id": token_id, "status": str(_token_status(row, now))},
                after={"token_id": token_id, "status": str(EnrollmentTokenStatus.REVOKED)},
            )
    tokens = list_enrollment_tokens(context).tokens
    return next(token for token in tokens if token.token_id == token_id_for(row["token_sha256"]))


def revoke_worker(context: OrchestratorContext, actor: AdminActor, worker_id: str) -> Worker:
    now = context.clock.now()
    with context.database.transaction() as connection:
        row = workers_repo.get_worker_row(connection, worker_id)
        if row is None:
            raise not_found("worker")
        if workers_repo.revoke_worker(connection, worker_id, now):
            ended = leases_repo.list_leases_for_worker(connection, worker_id)
            for lease in ended:
                end_lease(
                    connection,
                    lease,
                    now,
                    actor=actor.event_actor,
                    reason="worker revoked by an admin",
                    kind="lease_revoked",
                )
            held = leases_repo.list_holder_job_ids_for_worker(connection, worker_id)
            for job_id in held:
                leases_repo.remove_holder(connection, job_id, worker_id)
                events_repo.record_event(
                    connection,
                    now,
                    actor=actor.event_actor,
                    kind="holder_lost",
                    job_id=job_id,
                    worker_id=worker_id,
                    reason="worker revoked by an admin",
                )
            record_admin_change(
                connection,
                now,
                actor,
                "worker_revoked",
                f"{row['role']} '{row['name']}' revoked: {len(ended)} lease(s) ended,"
                f" holder of {len(held)} raw(s) dropped",
                before={"worker_id": worker_id, "name": row["name"], "revoked": False},
                after={"worker_id": worker_id, "name": row["name"], "revoked": True},
                worker_id=worker_id,
            )
        return workers_repo.worker_from_row(
            connection, workers_repo.get_worker_row(connection, worker_id)
        )
