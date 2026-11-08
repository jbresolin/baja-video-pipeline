"""Auth schemes and the standard error answers, shared by all contract routes.

The kinds of caller (docs/api.md, "Authentication"):
    workerBearer   per-worker token from /enroll (master, worker, studio)
    adminBearer    one static admin token from the orchestrator's config (scripts)
    sessionBearer  session token from POST /auth/login (review UI users,
                   role reviewer or admin)
    sessionCookie  the same session token in the bvp_session cookie; only
                   GET /auth/media reads it (Caddy forward_auth)
    webhookSecret  shared secret header configured in Evolution's webhook
"""

from typing import Any

from fastapi.security import APIKeyCookie, APIKeyHeader, HTTPBearer

from shared.models import ErrorResponse
from shared.protocol import WEBHOOK_SECRET_HEADER

# auto_error=False everywhere: in a contract stub nothing is enforced, and
# it lets a route list two schemes meaning "either one is accepted".
worker_bearer = HTTPBearer(
    scheme_name="workerBearer",
    description="Per-worker token returned by POST /enroll.",
    auto_error=False,
)
admin_bearer = HTTPBearer(
    scheme_name="adminBearer",
    description="Static admin token (BVP_ADMIN_TOKEN on the orchestrator).",
    auto_error=False,
)
session_bearer = HTTPBearer(
    scheme_name="sessionBearer",
    description=(
        "Session token from POST /auth/login. Review routes accept a user with role "
        "reviewer or admin; /admin/* routes need role admin (403 for a reviewer). "
        "Sessions end after 12 h without a request."
    ),
    auto_error=False,
)
session_cookie = APIKeyCookie(
    name="bvp_session",
    scheme_name="sessionCookie",
    description=(
        "The session token as the review UI's cookie. Read only by GET /auth/media, "
        "which Caddy's forward_auth calls with the browser's own headers."
    ),
    auto_error=False,
)
webhook_secret = APIKeyHeader(
    name=WEBHOOK_SECRET_HEADER,
    scheme_name="webhookSecret",
    description="Shared secret set as a custom header in Evolution's webhook config.",
    auto_error=False,
)

# The error answers a route can give, keyed by HTTP status. Every one uses
# the same {"error": {code, message, details}} body.
_ERROR_DESCRIPTIONS = {
    400: "Malformed request (validation_error)",
    401: "Missing or wrong credentials (unauthenticated)",
    403: "Authenticated but not allowed (forbidden)",
    404: "No such job / worker / team / user / ... (not_found)",
    409: "Conflicts with the current state (see error.code)",
    413: "Body too large (upload_too_large, inbox_full)",
    416: "The Range starts past the end of the file; Content-Range says its size",
    422: "Well-formed but wrong (hash_mismatch, missing_review_fields, unknown_prova, ...)",
    429: "Too many requests from this source IP (rate_limited); see Retry-After",
    503: "Temporarily unavailable (dev_mode_disabled, upstream_error)",
}


def error_responses(*statuses: int) -> dict[int | str, dict[str, Any]]:
    """The `responses=` value for a route that can fail with these statuses."""
    return {
        status: {"model": ErrorResponse, "description": _ERROR_DESCRIPTIONS[status]}
        for status in statuses
    }


def octet_stream_body(description: str) -> dict[str, Any]:
    """`openapi_extra` describing a raw binary request body (chunked uploads)."""
    return {
        "requestBody": {
            "required": True,
            "description": description,
            "content": {
                "application/octet-stream": {"schema": {"type": "string", "format": "binary"}}
            },
        }
    }


def not_implemented() -> None:
    """Body of every contract stub: the contract describes, it does not run."""
    raise NotImplementedError("contract stub; the orchestrator implements this route")
