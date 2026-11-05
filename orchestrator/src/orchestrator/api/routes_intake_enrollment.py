"""Routes: health, intake (Evolution webhook, dev upload) and enrollment."""

from typing import Any

from fastapi import APIRouter, BackgroundTasks, Body, Depends, File, Form, UploadFile
from fastapi.responses import JSONResponse

from orchestrator.api.auth import (
    get_context,
    require_admin,
    require_webhook_secret,
)
from orchestrator.api.error_handlers import error_response
from orchestrator.api.rate_limits import enforce_enroll_rate_limit
from orchestrator.context import OrchestratorContext
from orchestrator.domain import dev_upload, enrollment, intake
from orchestrator.domain.audit import AdminActor
from orchestrator.errors import ApiError
from shared.models import (
    CreateEnrollmentTokenRequest,
    EnrollmentTokenCreated,
    EnrollRequest,
    EnrollResponse,
    ErrorCode,
    IntakeResponse,
    ReceivedAs,
)

router = APIRouter()


@router.get("/health", tags=["meta"])
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.post(
    "/intake/evolution-webhook",
    tags=["intake"],
    response_model=IntakeResponse,
    status_code=202,
    dependencies=[Depends(require_webhook_secret)],
)
def evolution_webhook(
    background_tasks: BackgroundTasks,
    payload: Any = Body(...),
    context: OrchestratorContext = Depends(get_context),
):
    outcome = intake.handle_webhook(context, payload)
    # Replies and the download run after the answer is sent (Evolution
    # wants a fast 2xx); FastAPI runs these sync functions in a thread.
    if outcome.replies:
        background_tasks.add_task(intake.send_replies, context, outcome.replies)
    if outcome.download is not None:
        background_tasks.add_task(intake.run_media_download, context, outcome.download)
    if outcome.error is not None:
        response = error_response(outcome.error)
        response.background = background_tasks
        return response
    return JSONResponse(
        status_code=outcome.status_code,
        content=outcome.response.model_dump(mode="json"),
        background=background_tasks,
    )


@router.post(
    "/intake/test-upload",
    tags=["intake"],
    response_model=IntakeResponse,
    dependencies=[Depends(require_admin)],
)
def test_upload(
    file: UploadFile = File(...),
    sender_name: str | None = Form(None),
    received_as: ReceivedAs = Form(ReceivedAs.TEST_UPLOAD),
    context: OrchestratorContext = Depends(get_context),
) -> IntakeResponse:
    if not context.settings.dev_mode:
        raise ApiError(
            503, ErrorCode.DEV_MODE_DISABLED, "start the orchestrator with BVP_DEV_MODE=1"
        )
    return dev_upload.ingest_test_upload(
        context, file.file, file.filename, file.content_type, sender_name, received_as
    )


@router.post(
    "/admin/enrollment-tokens",
    tags=["enrollment"],
    response_model=EnrollmentTokenCreated,
    status_code=201,
)
def create_enrollment_token(
    body: CreateEnrollmentTokenRequest,
    actor: AdminActor = Depends(require_admin),
    context: OrchestratorContext = Depends(get_context),
) -> EnrollmentTokenCreated:
    return enrollment.create_enrollment_token(context, body, actor)


@router.post(
    "/enroll",
    tags=["enrollment"],
    response_model=EnrollResponse,
    status_code=201,
    dependencies=[Depends(enforce_enroll_rate_limit)],
)
def enroll(
    body: EnrollRequest, context: OrchestratorContext = Depends(get_context)
) -> EnrollResponse:
    return enrollment.enroll_worker(context, body)
