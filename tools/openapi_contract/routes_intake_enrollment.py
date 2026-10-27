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

# /enroll's errors, with the Retry-After header documented on the 429.
_ENROLL_ERRORS = error_responses(400, 401, 429, 503)
_ENROLL_ERRORS[429]["headers"] = {
    "Retry-After": {
        "description": "Seconds until this source IP may try again",
        "schema": {"type": "integer"},
    }
}


@router.get("/health", tags=["meta"], summary="Liveness check (no auth)")
def health() -> dict[str, str]:
    """Answers {"status": "ok"} when the process is up. For Caddy and monitoring."""
    not_implemented()


@router.post(
    "/intake/evolution-webhook",
    tags=["intake"],
    summary="Evolution instance webhook (messages.upsert) → background download → job",
    response_model=IntakeResponse,
    status_code=202,
    dependencies=[Depends(webhook_secret)],
    responses={
        200: {"model": IntakeResponse, "description": "Ignored or duplicate message"},
        **error_responses(400, 401, 413),
    },
)
def evolution_webhook(payload: EvolutionWebhook) -> IntakeResponse:
    """Exact route Caddy forwards: `POST /intake/evolution-webhook`, with the
    `X-Webhook-Secret` header (wrong or missing → 401).

    * Only `event == "messages.upsert"` with `data.key.fromMe == false` is
      considered; anything else gets 200 with `ignored: true`.
    * A `documentMessage` (also inside `documentWithCaptionMessage`) with a
      `video/*` mimetype → normal job. A `videoMessage` → `low_res: true`
      (the bot asks for a resend as document). Other messages → 200 ignored.
    * Answer fast: 202 `accepted: true`, then download the encrypted file
      from the WhatsApp CDN and decrypt it while streaming
      (`shared.whatsapp_media.download_incoming_media`) into the inbox. The
      job is created in state `received` only after every check passed;
      on failure no job is created and the bot asks the sender to resend.
    * Duplicate `data.key.id` (in progress or done) → 200 `duplicate: true`
      with the existing `job_id` if there is one.
    * Inbox above BVP_INBOX_CAP_GB → 413 `inbox_full`. Above
      BVP_INBOX_HOLD_GB the media is still accepted but the bot asks the
      sender to hold further videos.
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
    responses=_ENROLL_ERRORS,
)
def enroll(body: EnrollRequest) -> EnrollResponse:
    """No auth header: the enrollment token in the body is the credential.
    The one API route Caddy exposes publicly (design §8, §15).

    **Rate limit (enforced by the orchestrator, not Caddy):** at most
    10 requests per source IP per 600 s (BVP_ENROLL_RATE_LIMIT_REQUESTS,
    BVP_ENROLL_RATE_LIMIT_WINDOW_S), counting every request, successful or
    not. Over the limit → 429 `rate_limited` with a `Retry-After` header
    (seconds). The source IP is the last `X-Forwarded-For` entry when the
    request comes from Caddy, else the TCP peer address.

    The token is marked used on success, so a second call with it gets 401
    `enrollment_token_invalid` (also for unknown or expired tokens). The
    Tailscale key is minted through the Tailscale API (OAuth client,
    auth-keys scope) as single-use, pre-approved, non-ephemeral and tagged
    `tag:worker` (or `tag:studio` for the studio role). If the Tailscale API
    fails: 503 `upstream_error` and the token stays unused.
    """
    not_implemented()
