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
from review_ui.media_info_view import media_panel
from review_ui.orchestrator_client import (
    OrchestratorClient,
    OrchestratorError,
    OrchestratorUnavailableError,
)
from review_ui.request_context import orchestrator_for, render_page
from review_ui.settings import JOB_PANEL_POLL_SECONDS
from shared.models import Job, Team

router = APIRouter()


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


def prova_choices(orchestrator: OrchestratorClient, job: Job) -> list[str]:
    """The prova select's options: the active provas in the admin's order.

    The job's current prova is added at the end when it is not among them
    (deactivated since, or typed before the list existed), so opening the
    page never silently changes it. The orchestrator only accepts provas
    from its list when a job is made ready.
    """
    choices = [prova.name for prova in orchestrator.provas() if prova.active]
    if job.prova and job.prova not in choices:
        choices.append(job.prova)
    return choices


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
        media=media_panel(job),
        actions=[(action.value, ACTION_LABELS[action]) for action in available_actions(job.state)],
        prova_choices=prova_choices(orchestrator, job),
        team=team_or_none(orchestrator, job),
        poll_seconds=JOB_PANEL_POLL_SECONDS,
    )
    return render_page(request, "job.html", context)


def team_or_none(orchestrator: OrchestratorClient, job: Job) -> Team | None:
    """The job's team with the names its clip shows (LIVESTACK-42), or None.

    None for a job without a car number, a car the teams table does not
    know, or a failed lookup: the page then shows the job's own team name.
    """
    if job.car_number is None:
        return None
    try:
        return orchestrator.get_team(job.car_number)
    except (OrchestratorError, OrchestratorUnavailableError):
        return None


@router.get("/review/jobs/{job_id}/panel", response_class=HTMLResponse)
def job_panel_partial(request: Request, job_id: str) -> HTMLResponse:
    """Status strip and event log only, refreshed by the page every few seconds."""
    orchestrator = orchestrator_for(request)
    job = orchestrator.get_job(job_id)
    return render_page(request, "_job_panel.html", panel_context(job, orchestrator))
