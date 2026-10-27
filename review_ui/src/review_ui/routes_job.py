"""The job page (/review/jobs/{id}) and the status panel it polls."""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from review_ui.job_view import (
    ACTION_LABELS,
    available_actions,
    build_timeline,
    explain_status,
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


def panel_context(job: Job, worker_names: dict[str, str]) -> dict:
    return {
        "job": job,
        "notes": explain_status(job, worker_names),
        "timeline": build_timeline(job, worker_names),
    }


@router.get("/review/jobs/{job_id}", response_class=HTMLResponse)
def job_page(request: Request, job_id: str) -> HTMLResponse:
    orchestrator = orchestrator_for(request)
    # The job itself must load; a 404 or 401 here becomes the error page.
    job = orchestrator.get_job(job_id)
    context = panel_context(job, worker_names_or_empty(orchestrator))
    context.update(
        active_nav="queue",
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
    context = panel_context(job, worker_names_or_empty(orchestrator))
    return render_page(request, "_job_panel.html", context)
