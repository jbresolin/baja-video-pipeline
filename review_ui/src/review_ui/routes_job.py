"""The job page (/review/jobs/{id}) and the status panel it polls."""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from review_ui.event_log_view import EventLine, event_lines_newest_first
from review_ui.job_view import (
    ACTION_LABELS,
    available_actions,
    explain_status,
    frame_rate_for_stepping,
    intake_text,
)
from review_ui.orchestrator_client import (
    OrchestratorClient,
    OrchestratorError,
    OrchestratorUnavailableError,
)
from review_ui.queue_view import distinct_provas
from review_ui.request_context import orchestrator_for, render_page
from review_ui.settings import JOB_PANEL_POLL_SECONDS
from shared.models import Job, JobState

router = APIRouter()

# Jobs whose prova a reviewer already typed: a source of datalist suggestions.
_REVIEWED_STATES = [
    JobState.AWAITING_REVIEW,
    JobState.READY,
    JobState.CLAIMED,
    JobState.UPLOADED,
    JobState.DELIVERED,
    JobState.DONE,
]
# Enough suggestions for a day of provas without making the list unwieldy.
MAX_PROVA_SUGGESTIONS = 20


def worker_names_or_empty(orchestrator: OrchestratorClient) -> dict[str, str]:
    """Worker id → name from the fleet, to show names instead of ids.

    Nice to have only: if the fleet call fails, the page still works and
    shows short ids.
    """
    try:
        fleet = orchestrator.fleet()
    except (OrchestratorError, OrchestratorUnavailableError):
        return {}
    return {entry.worker.id: entry.worker.name for entry in fleet.workers}


def recent_provas_or_empty(orchestrator: OrchestratorClient) -> list[str]:
    """Provas used recently (delivered clips first), for the prova field's datalist."""
    provas: list[str | None] = []
    try:
        provas += [clip.prova for clip in orchestrator.manifest().clips]
        provas += [item.prova for item in orchestrator.review_queue(_REVIEWED_STATES)]
    except (OrchestratorError, OrchestratorUnavailableError):
        pass  # Suggestions are optional; whatever was collected is still useful.
    return distinct_provas(provas)[:MAX_PROVA_SUGGESTIONS]


def event_lines_or_none(
    orchestrator: OrchestratorClient, job_id: str, worker_names: dict[str, str]
) -> list[EventLine] | None:
    """The job's event log, newest first; None when it cannot be read.

    The log is secondary to reviewing: if the call fails, the page still
    renders (with an "event log unavailable" note) so the reviewer can work.
    """
    try:
        events = orchestrator.job_events(job_id)
    except (OrchestratorError, OrchestratorUnavailableError):
        return None
    return event_lines_newest_first(events, worker_names)


def panel_context(job: Job, orchestrator: OrchestratorClient) -> dict:
    """What the status strip and event log need (the page and its refresh share it)."""
    worker_names = worker_names_or_empty(orchestrator)
    return {
        "job": job,
        "notes": explain_status(job, worker_names),
        # None means "could not load"; an empty list means "no events yet".
        "event_lines": event_lines_or_none(orchestrator, job.id, worker_names),
    }


@router.get("/review/jobs/{job_id}", response_class=HTMLResponse)
def job_page(request: Request, job_id: str) -> HTMLResponse:
    orchestrator = orchestrator_for(request)
    # The job itself must load; a 404 or 401 here becomes the error page.
    job = orchestrator.get_job(job_id)
    context = panel_context(job, orchestrator)
    context.update(
        active_nav="queue",
        intake=intake_text(job),
        frame_rate=frame_rate_for_stepping(job),
        actions=[(action.value, ACTION_LABELS[action]) for action in available_actions(job.state)],
        prova_suggestions=recent_provas_or_empty(orchestrator),
        poll_seconds=JOB_PANEL_POLL_SECONDS,
    )
    return render_page(request, "job.html", context)


@router.get("/review/jobs/{job_id}/panel", response_class=HTMLResponse)
def job_panel_partial(request: Request, job_id: str) -> HTMLResponse:
    """Status strip and event log only, refreshed by the page every few seconds."""
    orchestrator = orchestrator_for(request)
    job = orchestrator.get_job(job_id)
    return render_page(request, "_job_panel.html", panel_context(job, orchestrator))
