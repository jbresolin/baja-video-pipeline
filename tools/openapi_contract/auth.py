"""Auth schemes and the standard error answers, shared by all contract routes.

Four kinds of caller (docs/api.md, "Authentication"):
    workerBearer   per-worker token from /enroll (master, worker, studio)
    adminBearer    one static admin token from the orchestrator's config
    reviewerBasic  HTTP Basic, checked against BVP_REVIEWERS_FILE (bcrypt)
    webhookSecret  shared secret header configured in Evolution's webhook
"""

from typing import Any

from fastapi.security import APIKeyHeader, HTTPBasic, HTTPBearer

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
reviewer_basic = HTTPBasic(
    scheme_name="reviewerBasic",
    description=(
        "Reviewer username and password, checked against the bcrypt hashes in "
        "BVP_REVIEWERS_FILE (the same file Caddy imports). See docs/api.md."
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
    404: "No such job / worker / team (not_found)",
    409: "Conflicts with the current state (see error.code)",
    413: "Body too large (upload_too_large, inbox_full)",
    422: "Well-formed but wrong (hash_mismatch, missing_review_fields, ...)",
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
