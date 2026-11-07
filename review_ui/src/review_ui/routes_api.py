"""JSON endpoints called by the job page's scripts.

The browser cannot reach the orchestrator's API (Caddy only routes /review*
to this service), so these endpoints relay the calls, as the same reviewer.

Every endpoint that changes something requires a JSON body. Browsers send
the session cookie on same-site requests only (SameSite=Lax), and
a plain HTML form on another site cannot send `Content-Type:
application/json` without a CORS preflight, which we never allow. That
stops cross-site request forgery without tokens.
"""

import json

from fastapi import APIRouter, Path, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from review_ui.catalog import text
from review_ui.errors import UiRequestError
from review_ui.job_view import describe_waiting_reason, save_message
from review_ui.request_context import orchestrator_for, render_fragment
from review_ui.routes_job import build_passagem_help
from review_ui.templating import media_url
from shared.models import ReviewAction, ReviewUpdate

router = APIRouter(prefix="/review/api")

# What the page says after each successful action.
SUCCESS_MESSAGES = {action: text(f"action_done.{action.value}") for action in ReviewAction}


async def read_json_body(request: Request) -> dict:
    content_type = request.headers.get("content-type", "")
    if content_type.split(";")[0].strip().lower() != "application/json":
        raise UiRequestError(415, "validation_error", text("error.send_as_json"))
    try:
        body = json.loads(await request.body() or b"{}")
    except json.JSONDecodeError as error:
        raise UiRequestError(400, "validation_error", text("error.body_not_json")) from error
    if not isinstance(body, dict):
        raise UiRequestError(400, "validation_error", text("error.body_not_object"))
    return body


@router.patch("/jobs/{job_id}/review")
async def update_review(request: Request, job_id: str) -> JSONResponse:
    body = await read_json_body(request)
    try:
        update = ReviewUpdate.model_validate(body)
    except ValidationError as error:
        # Checked here too so an obviously bad request (e.g. out <= in)
        # gets a clear message without a round trip.
        # Pydantic's sentences are English: they go after the pt-BR words.
        messages = "; ".join(item["msg"] for item in error.errors())
        people_text = f"{text('error.validation_error')} ({messages})"
        raise UiRequestError(400, "validation_error", people_text) from error
    orchestrator = orchestrator_for(request)
    # The orchestrator call blocks, so it runs in a worker thread.
    job = await run_in_threadpool(orchestrator.update_review, job_id, update)
    message = SUCCESS_MESSAGES[update.action]
    if update.action == ReviewAction.SAVE:
        message = save_message(job)
    return JSONResponse(
        {
            "job": job.model_dump(mode="json"),
            "message": message,
            # A save on a job with a clip reloads the page, so the warning
            # about the clip on air (LIVESTACK-43) appears at once.
            "refresh_page": update.action == ReviewAction.SAVE and job.clip_inputs is not None,
        }
    )


@router.post("/jobs/{job_id}/regenerate-proxy")
async def regenerate_proxy(request: Request, job_id: str) -> JSONResponse:
    await read_json_body(request)
    orchestrator = orchestrator_for(request)
    answer = await run_in_threadpool(orchestrator.regenerate_proxy, job_id)
    body = answer.model_dump(mode="json")
    if answer.waiting_reason:
        # The orchestrator's reason is English; the page shows this instead.
        described = describe_waiting_reason(answer.waiting_reason)
        body["waiting_text"] = " ".join(part for part in (described.text, described.detail) if part)
    return JSONResponse(body, status_code=202)


def _original_answer(request: Request, answer) -> JSONResponse:
    """The orchestrator's OriginalDownload, with a link the browser can use."""
    body = answer.model_dump(mode="json")
    body["download_url"] = media_url(
        request.app.state.settings.public_base_url, answer.download_url
    )
    return JSONResponse(body)


@router.post("/jobs/{job_id}/original")
async def request_original(request: Request, job_id: str) -> JSONResponse:
    """ "Baixar original" (LIVESTACK-41 item 15)."""
    await read_json_body(request)
    orchestrator = orchestrator_for(request)
    return _original_answer(request, await run_in_threadpool(orchestrator.request_original, job_id))


@router.get("/jobs/{job_id}/original")
async def original_status(request: Request, job_id: str) -> JSONResponse:
    """Polled by the job page while the original is being prepared."""
    orchestrator = orchestrator_for(request)
    return _original_answer(request, await run_in_threadpool(orchestrator.get_original, job_id))


@router.get("/jobs/{job_id}/passagem-help")
def passagem_help(
    request: Request,
    job_id: str,
    prova: str = Query("", max_length=200),
    car_number: int | None = Query(None, ge=0, le=9999),
    passagem: int | None = Query(None, ge=1, le=999),
) -> JSONResponse:
    """The "Outros vídeos de <prova>, carro <n>" panel for what the form says
    now, saved or not (LIVESTACK-45). The page swaps `html` in and shows
    `warnings` next to the passagem field and the "Pronto" button."""
    orchestrator = orchestrator_for(request)
    job = orchestrator.get_job(job_id)
    panel = build_passagem_help(orchestrator, job, prova, car_number, passagem)
    return JSONResponse(
        {
            "html": render_fragment(request, "_passagem_help.html", {"help": panel}),
            "warnings": panel.warnings if panel else [],
            "suggestion": panel.suggestion if panel else None,
        }
    )


@router.get("/teams/{car_number}")
def get_team(request: Request, car_number: int = Path(ge=0, le=9999)) -> JSONResponse:
    team = orchestrator_for(request).get_team(car_number)
    if team is None:
        message = text("error.car_not_in_teams", car_number=car_number)
        raise UiRequestError(404, "not_found", message)
    return JSONResponse(team.model_dump(mode="json"))
