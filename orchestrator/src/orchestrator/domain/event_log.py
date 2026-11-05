"""GET /jobs/{id}/events, GET /events and GET /admin/audit: the event log for humans."""

from datetime import UTC, datetime

from orchestrator.context import OrchestratorContext
from orchestrator.domain.audit import KINDS_BY_AREA
from orchestrator.domain.job_view import require_job_row
from orchestrator.repositories import events as events_repo
from shared.models import AdminArea, Event, EventsResponse


def to_api_event(event: events_repo.Event) -> Event:
    return Event(
        id=event.id,
        at=event.at,
        job_id=event.job_id,
        worker_id=event.worker_id,
        actor=event.actor,
        kind=event.kind,
        from_state=event.from_state,
        to_state=event.to_state,
        reason=event.reason,
        details=event.details,
    )


def job_events(context: OrchestratorContext, job_id: str) -> EventsResponse:
    """Everything that happened to one job, oldest first; 404 for an unknown job."""
    with context.database.transaction() as connection:
        require_job_row(connection, job_id)
        events = events_repo.list_events_for_job(connection, job_id)
    return EventsResponse(events=[to_api_event(event) for event in events])


def recent_events(
    context: OrchestratorContext, worker_id: str | None, since: datetime | None, limit: int
) -> EventsResponse:
    """The log across jobs, optionally for one worker; see events_repo.list_events."""
    if since is not None and since.tzinfo is None:
        since = since.replace(tzinfo=UTC)  # docs/api.md §1: times are UTC
    with context.database.transaction() as connection:
        events = events_repo.list_events(connection, worker_id=worker_id, since=since, limit=limit)
    return EventsResponse(events=[to_api_event(event) for event in events])


def recent_admin_changes(
    context: OrchestratorContext, area: AdminArea | None, limit: int
) -> EventsResponse:
    """GET /admin/audit: the newest admin changes, NEWEST first, optionally of one page."""
    kinds = KINDS_BY_AREA[area] if area else None
    with context.database.transaction() as connection:
        events = events_repo.list_admin_events(connection, kinds, limit)
    return EventsResponse(events=[to_api_event(event) for event in events])
