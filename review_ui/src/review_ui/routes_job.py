"""The job page (/review/jobs/{id}) and the status panel it polls."""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from review_ui.clip_inputs_view import outdated_clip
from review_ui.event_log_view import EventLine, event_lines_newest_first
from review_ui.job_view import (
    action_buttons,
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
from review_ui.pass_clock_view import overlay_preview_data, pass_clock_box
from review_ui.pass_result_view import pass_result_box
from review_ui.passagem_help_view import PassagemHelp, passagem_help
from review_ui.presence_view import presence_sentence
from review_ui.render_options_view import render_options_box
from review_ui.request_context import orchestrator_for, render_page
from review_ui.sender_messages_view import SenderMessagesSection, sender_messages_section
from review_ui.settings import JOB_PANEL_POLL_SECONDS
from shared.models import Job, Team, utc_now

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
        # The clip on air no longer matches the review (LIVESTACK-43), or None.
        "outdated": outdated_clip(job.clip_inputs, job.state),
        # None means "could not load"; an empty list means "no events yet".
        "event_lines": event_lines_or_none(orchestrator, job.id, worker_names),
    }


@router.get("/review/jobs/{job_id}", response_class=HTMLResponse)
def job_page(request: Request, job_id: str) -> HTMLResponse:
    orchestrator = orchestrator_for(request)
    # The job itself must load; a 404 or 401 here becomes the error page.
    job = orchestrator.get_job(job_id)
    context = panel_context(job, orchestrator)
    now = utc_now()
    pass_box = pass_result_box(job.pass_result, now)
    context.update(
        active_nav="queue",
        intake=intake_text(job),
        frame_rate=frame_rate_for_stepping(job),
        media=media_panel(job),
        # "Imagem e som": Girar, Enquadramento, Sem áudio (LIVESTACK-50).
        render_box=render_options_box(job),
        # "Mostrar resultado da passagem" and its status line (LIVESTACK-47),
        # or None when the job's prova shows no result.
        pass_box=pass_box,
        # "Cronômetro": the largada mark and "Mostrar cronômetro" (LIVESTACK-48),
        # or None when the job's prova has no clock.
        clock_box=pass_clock_box(job.pass_clock, now, result_box_shown=pass_box is not None),
        # What "Pré-visualizar overlay" draws over the player (LIVESTACK-48).
        preview=overlay_preview_data(job),
        actions=action_buttons(job.state),
        prova_choices=prova_choices(orchestrator, job),
        team=team_or_none(orchestrator, job),
        # "Outros vídeos de <prova>, carro <n>" for the saved values (LIVESTACK-45).
        help=passagem_help_or_none(orchestrator, job),
        poll_seconds=JOB_PANEL_POLL_SECONDS,
    )
    # "Mensagens de <remetente>" (LIVESTACK-49).
    context["messages"], context["messages_failed"] = sender_messages_or_none(orchestrator, job)
    # Someone else already on this video (LIVESTACK-51): the banner, and the
    # form waits for "Revisar mesmo assim".
    context["presence"] = presence_or_none(orchestrator, job)
    return render_page(request, "job.html", context)


def presence_or_none(orchestrator: OrchestratorClient, job: Job) -> str | None:
    """Opening the page is this reviewer's first ping; the sentence naming the
    others on the video, or None.

    Presence only informs: if the call fails, the page opens as if nobody
    else were on it (the script tries again in 20 s).
    """
    try:
        return presence_sentence(orchestrator.ping_presence(job.id))
    except (OrchestratorError, OrchestratorUnavailableError):
        return None


def sender_messages_or_none(
    orchestrator: OrchestratorClient, job: Job
) -> tuple[SenderMessagesSection | None, bool]:
    """(the section or None, whether asking failed).

    Context for the reviewer, not essential: if the call fails, the page
    still renders and says the messages could not be loaded.
    """
    try:
        response = orchestrator.sender_messages(job.id)
    except (OrchestratorError, OrchestratorUnavailableError):
        return None, True
    return sender_messages_section(response, job), False


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


def build_passagem_help(
    orchestrator: OrchestratorClient,
    job: Job,
    prova: str | None,
    car_number: int | None,
    passagem: int | None,
) -> PassagemHelp | None:
    """The passagem panel for these form values; None until prova and car are set."""
    if not prova or not prova.strip() or car_number is None:
        return None
    videos = orchestrator.car_videos(prova.strip(), car_number)
    return passagem_help(job, prova.strip(), car_number, videos, passagem)


def passagem_help_or_none(orchestrator: OrchestratorClient, job: Job) -> PassagemHelp | None:
    """The panel for the job's saved values, for the first page load.

    Helpful, not essential: if the call fails, the page still renders and
    the script tries again as soon as the reviewer changes a field.
    """
    try:
        return build_passagem_help(orchestrator, job, job.prova, job.car_number, job.passagem)
    except (OrchestratorError, OrchestratorUnavailableError):
        return None


@router.get("/review/jobs/{job_id}/panel", response_class=HTMLResponse)
def job_panel_partial(request: Request, job_id: str) -> HTMLResponse:
    """Status strip and event log only, refreshed by the page every few seconds."""
    orchestrator = orchestrator_for(request)
    job = orchestrator.get_job(job_id)
    return render_page(request, "_job_panel.html", panel_context(job, orchestrator))
