"""Review-side reads and requests that do not edit the review: queue, job, team, proxy."""

from orchestrator.clients.baja_app import BajaAppError
from orchestrator.context import OrchestratorContext
from orchestrator.domain.fleet_snapshot import FleetSnapshot
from orchestrator.domain.job_view import job_from_row, load_job, require_job_row
from orchestrator.errors import not_found, upstream_error
from orchestrator.repositories import events as events_repo
from orchestrator.repositories import jobs as jobs_repo
from orchestrator.repositories import leases as leases_repo
from shared.models import (
    HolderStatus,
    Job,
    JobState,
    JobSummary,
    RegenerateProxyResponse,
    ReviewQueueResponse,
    Team,
)

NO_HOLDER_ONLINE = "waiting for a holder of the raw to come back online"


def review_queue(context: OrchestratorContext, states: list[JobState], limit: int):
    with context.database.transaction() as connection:
        rows = jobs_repo.list_job_rows_in_states(connection, states, limit)
        jobs = [job_from_row(connection, row) for row in rows]
    return ReviewQueueResponse(items=[JobSummary.from_job(job) for job in jobs])


def get_job(context: OrchestratorContext, job_id: str) -> Job:
    with context.database.transaction() as connection:
        return load_job(connection, job_id)


def find_team(context: OrchestratorContext, car_number: int) -> Team:
    try:
        team = context.baja_app.find_team(car_number)
    except BajaAppError as error:
        raise upstream_error("baja app", str(error)) from error
    if team is None:
        raise not_found("team for that car number")
    return team


def regenerate_proxy(
    context: OrchestratorContext, reviewer: str, job_id: str
) -> RegenerateProxyResponse:
    with context.database.transaction() as connection:
        now = context.clock.now()
        job_row = require_job_row(connection, job_id)
        if job_row["state"] in (JobState.DISCARDED, JobState.FAILED):
            reason = f"job is {job_row['state']}; no proxy will be rendered"
            return RegenerateProxyResponse(job_id=job_id, queued=False, waiting_reason=reason)
        snapshot = FleetSnapshot.take(connection, now)
        holder_online = any(
            holder.status == HolderStatus.CONFIRMED and snapshot.is_online(holder.worker_id)
            for holder in leases_repo.list_holders(connection, job_id)
        )
        waiting_reason = None if holder_online else NO_HOLDER_ONLINE
        jobs_repo.update_job(
            connection, job_id, now, {"proxy_requested": 1, "waiting_reason": waiting_reason}
        )
        events_repo.record_event(
            connection,
            now,
            actor=events_repo.reviewer_actor(reviewer),
            kind="proxy_requested",
            job_id=job_id,
        )
    return RegenerateProxyResponse(job_id=job_id, queued=True, waiting_reason=waiting_reason)
