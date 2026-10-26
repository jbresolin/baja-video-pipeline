"""Contract: intake (Evolution webhook, dev upload), enrollment and health."""

from fastapi import APIRouter, Depends, File, Form, UploadFile

from shared.models import (
    CreateEnrollmentTokenRequest,
    EnrollmentTokenCreated,
    EnrollRequest,
    EnrollResponse,
    EvolutionWebhook,
    IntakeResponse,
    ReceivedAs,
)
from tools.openapi_contract.auth import (
    admin_bearer,
    error_responses,
    not_implemented,
    webhook_secret,
)

router = APIRouter()


@router.get("/health", tags=["meta"], summary="Liveness check (no auth)")
def health() -> dict[str, str]:
    """Answers {"status": "ok"} when the process is up. For Caddy and monitoring."""
    not_implemented()


@router.post(
    "/intake/evolution-webhook",
    tags=["intake"],
    summary="Evolution messages.upsert webhook → job",
    response_model=IntakeResponse,
    dependencies=[Depends(webhook_secret)],
    responses=error_responses(400, 401, 413, 503),
)
def evolution_webhook(payload: EvolutionWebhook) -> IntakeResponse:
    """Turn a WhatsApp video into a job.

    * Only `event == "messages.upsert"` with `data.key.fromMe == false` is
      considered; anything else gets 200 with `ignored: true`.
    * A `documentMessage` with a `video/*` mimetype → normal job. A
      `videoMessage` → job with `low_res: true` (and the bot asks for a
      resend as document). Other messages → `ignored: true`.
    * The raw is downloaded from `data.message.mediaUrl` (Evolution S3/MinIO
      media storage) into the inbox, SHA-256 computed, job created in
      state `received`.
    * Duplicate `data.key.id` → 200 with `duplicate: true` and the existing
      `job_id` (Evolution retries webhooks).
    * Inbox above its hard cap → 413 `inbox_full` (the bot tells the sender
      to hold).
    """
    not_implemented()


@router.post(
    "/intake/test-upload",
    tags=["intake"],
    summary="Dev only: upload a file as if it came from WhatsApp",
    response_model=IntakeResponse,
    dependencies=[Depends(admin_bearer)],
    responses=error_responses(400, 401, 413, 503),
)
def test_upload(
    file: UploadFile = File(..., description="The video file."),
    sender_name: str | None = Form(None),
    received_as: ReceivedAs = Form(
        ReceivedAs.TEST_UPLOAD, description='Send "video" to simulate a low_res intake.'
    ),
) -> IntakeResponse:
    """Same result as the webhook, from a multipart upload.

    Enabled only when the orchestrator runs with BVP_DEV_MODE=1; otherwise
    503 `dev_mode_disabled`.
    """
    not_implemented()


@router.post(
    "/admin/enrollment-tokens",
    tags=["enrollment"],
    summary="Create a one-time enrollment token for a volunteer",
    response_model=EnrollmentTokenCreated,
    status_code=201,
    dependencies=[Depends(admin_bearer)],
    responses=error_responses(400, 401),
)
def create_enrollment_token(body: CreateEnrollmentTokenRequest) -> EnrollmentTokenCreated:
    """The plaintext token is returned once; only its SHA-256 is stored."""
    not_implemented()


@router.post(
    "/enroll",
    tags=["enrollment"],
    summary="Exchange a one-time token for worker id, bearer token and Tailscale key",
    response_model=EnrollResponse,
    status_code=201,
    responses=error_responses(400, 401, 503),
)
def enroll(body: EnrollRequest) -> EnrollResponse:
    """No auth header: the enrollment token in the body is the credential.

    The token is marked used on success, so a second call with it gets 401
    `enrollment_token_invalid` (also for unknown or expired tokens). The
    Tailscale key is minted through the Tailscale API (OAuth client,
    auth-keys scope) as single-use, pre-approved, non-ephemeral and tagged
    `tag:worker` (or `tag:studio` for the studio role). If the Tailscale API
    fails: 503 `upstream_error` and the token stays unused.
    """
    not_implemented()
