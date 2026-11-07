"""Review-side reads and requests that do not edit the review: queue, job, team, proxy."""

from orchestrator.context import OrchestratorContext
from orchestrator.domain.clip_names import clip_names, name_styles
from orchestrator.domain.fleet_snapshot import FleetSnapshot
from orchestrator.domain.job_view import job_from_row, load_job, require_job_row
from orchestrator.errors import not_found
from orchestrator.repositories import events as events_repo
from orchestrator.repositories import jobs as jobs_repo
from orchestrator.repositories import leases as leases_repo
from orchestrator.repositories import teams as teams_repo
from shared.models import (
    HolderStatus,
    Job,
    JobState,
    JobSummary,
    RegenerateProxyResponse,
    ReviewQueueResponse,
    Team,
)

NO_HOLDER_ONLINE = "waiting for a holder of the raw to come back online (or be resumed)"


def review_queue(
    context: OrchestratorContext, states: list[JobState], limit: int, *, outdated_only=False
):
    """Jobs in `states` in filming order, oldest recording first (LIVESTACK-45);
    with `outdated_only`, only those whose newest clip no longer matches the
    review (LIVESTACK-43, "Clipes desatualizados"), filtered before the limit
    so none is cut off."""
    with context.database.transaction() as connection:
        if outdated_only:
            rows = jobs_repo.list_review_queue_rows(connection, states)
            jobs = [job_from_row(connection, row, context.layout) for row in rows]
            jobs = [job for job in jobs if job.clip_inputs and job.clip_inputs.changed][:limit]
        else:
            rows = jobs_repo.list_review_queue_rows(connection, states, limit)
            jobs = [job_from_row(connection, row, context.layout) for row in rows]
    return ReviewQueueResponse(items=[JobSummary.from_job(job) for job in jobs])


def holder_available(connection, job_id: str, now) -> bool:
    """True when a confirmed holder of the raw is online and not paused."""
    snapshot = FleetSnapshot.take(connection, now)
    return any(
        holder.status == HolderStatus.CONFIRMED and snapshot.is_available_for_work(holder.worker_id)
        for holder in leases_repo.list_holders(connection, job_id)
    )


def get_job(context: OrchestratorContext, job_id: str) -> Job:
    with context.database.transaction() as connection:
        return load_job(connection, job_id, context.layout)


def find_team(context: OrchestratorContext, car_number: int) -> Team:
    """GET /teams/{car}: the teams table (synced from the results API, plus corrections)."""
    with context.database.transaction() as connection:
        team = teams_repo.get_team(connection, car_number)
        styles = name_styles(connection)
    if team is None:
        raise not_found("team for that car number")
    # How a clip of this car would name the team and school (LIVESTACK-42).
    names = clip_names(
        styles,
        team_full=team.team_name,
        team_short=team.short_name,
        school_full=team.school,
        school_short=team.school_short,
    )
    return team.model_copy(update={"clip_team_name": names.team, "clip_school_name": names.school})


def regenerate_proxy(
    context: OrchestratorContext, reviewer: str, job_id: str
) -> RegenerateProxyResponse:
    with context.database.transaction() as connection:
        now = context.clock.now()
        job_row = require_job_row(connection, job_id)
        if job_row["state"] in (JobState.DISCARDED, JobState.FAILED):
            reason = f"job is {job_row['state']}; no proxy will be rendered"
            return RegenerateProxyResponse(job_id=job_id, queued=False, waiting_reason=reason)
        waiting_reason = None if holder_available(connection, job_id, now) else NO_HOLDER_ONLINE
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
