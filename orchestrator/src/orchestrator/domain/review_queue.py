"""Review-side reads and requests that do not edit the review: queue, job, team, proxy."""

import sqlite3

from orchestrator.context import OrchestratorContext
from orchestrator.domain import contacts, sender_messages
from orchestrator.domain.clip_names import clip_names, name_styles
from orchestrator.domain.fleet_snapshot import FleetSnapshot
from orchestrator.domain.job_view import job_from_row, load_job, require_job_row
from orchestrator.errors import not_found
from orchestrator.repositories import contacts as contacts_repo
from orchestrator.repositories import events as events_repo
from orchestrator.repositories import jobs as jobs_repo
from orchestrator.repositories import leases as leases_repo
from orchestrator.repositories import teams as teams_repo
from shared.models import (
    HolderStatus,
    Job,
    JobPresenceResponse,
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
        counts = sender_messages.message_counts(connection, rows)
        summaries = with_senders(connection, summaries_with_message_counts(jobs, counts), jobs)
    # Who has each video open now (LIVESTACK-51), so people pick another.
    reviewing = context.presence.everyone(context.clock.now())
    summaries = [
        summary.model_copy(update={"reviewing": reviewing.get(summary.id, [])})
        for summary in summaries
    ]
    return ReviewQueueResponse(items=summaries)


def ping_presence(context: OrchestratorContext, reviewer: str, job_id: str) -> JobPresenceResponse:
    """PUT /jobs/{id}/presence: `reviewer` is on the job; the others on it (domain/presence.py)."""
    with context.database.transaction() as connection:
        require_job_row(connection, job_id)
    others = context.presence.ping(job_id, reviewer, context.clock.now())
    return JobPresenceResponse(others=others)


def with_senders(
    connection: sqlite3.Connection, summaries: list[JobSummary], jobs: list[Job]
) -> list[JobSummary]:
    """Each summary with its sender's number and name, for the "Enviado por" filter.

    The name is the one on Admin → Contatos, else the WhatsApp profile
    name the job arrived with (LIVESTACK-51 item 5).
    """
    names = contacts_repo.display_names(connection)
    filled = []
    for summary, job in zip(summaries, jobs, strict=True):
        phone = contacts.sender_phone(job.source.sender_jid)
        display_name = names.get(phone) if phone else None
        filled.append(
            summary.model_copy(
                update={
                    "sender_phone": phone,
                    "sender_display_name": display_name or job.source.sender_name,
                }
            )
        )
    return filled


def summaries_with_message_counts(jobs: list[Job], counts: dict[str, int]) -> list[JobSummary]:
    """JobSummary of each job, with how many sender messages its page lists (LIVESTACK-49)."""
    return [
        JobSummary.from_job(job).model_copy(update={"sender_message_count": counts.get(job.id, 0)})
        for job in jobs
    ]


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
