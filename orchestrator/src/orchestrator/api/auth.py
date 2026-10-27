"""The four credentials (docs/api.md §2) as FastAPI dependencies.

    workerBearer   per-worker token from /enroll       → the worker's DB row
    adminBearer    BVP_ADMIN_TOKEN                      → "admin"
    reviewerBasic  BVP_REVIEWERS_FILE (bcrypt)          → the reviewer's username
    webhookSecret  X-Webhook-Secret = BVP_WEBHOOK_SECRET

The security objects are declared with the same scheme names as the
contract, so the service's own OpenAPI document matches orchestrator/openapi.yaml.
"""

import sqlite3
from dataclasses import dataclass

from fastapi import Depends, Request
from fastapi.security import (
    APIKeyHeader,
    HTTPAuthorizationCredentials,
    HTTPBasic,
    HTTPBasicCredentials,
    HTTPBearer,
)

from orchestrator.context import OrchestratorContext
from orchestrator.errors import forbidden, unauthenticated
from orchestrator.repositories import workers as workers_repo
from orchestrator.security import secrets_match, token_sha256
from shared.protocol import WEBHOOK_SECRET_HEADER

worker_bearer = HTTPBearer(scheme_name="workerBearer", auto_error=False)
admin_bearer = HTTPBearer(scheme_name="adminBearer", auto_error=False)
reviewer_basic = HTTPBasic(scheme_name="reviewerBasic", auto_error=False)
webhook_secret = APIKeyHeader(
    name=WEBHOOK_SECRET_HEADER, scheme_name="webhookSecret", auto_error=False
)


def get_context(request: Request) -> OrchestratorContext:
    return request.app.state.context


def _worker_for_token(context: OrchestratorContext, token: str) -> sqlite3.Row | None:
    with context.database.transaction() as connection:
        return workers_repo.find_worker_by_token_hash(connection, token_sha256(token))


def _is_admin(context: OrchestratorContext, token: str) -> bool:
    return secrets_match(token, context.settings.admin_token.get_secret_value())


def _reviewer_name(
    context: OrchestratorContext, credentials: HTTPBasicCredentials | None
) -> str | None:
    if credentials is None:
        return None
    if context.reviewers.check(credentials.username, credentials.password):
        return credentials.username
    return None


def require_worker(
    credentials: HTTPAuthorizationCredentials | None = Depends(worker_bearer),
    context: OrchestratorContext = Depends(get_context),
) -> sqlite3.Row:
    if credentials is None:
        raise unauthenticated("send 'Authorization: Bearer <worker token>'")
    worker_row = _worker_for_token(context, credentials.credentials)
    if worker_row is None:
        raise unauthenticated("unknown worker token")
    return worker_row


def require_worker_matching_path(
    worker_id: str, worker_row: sqlite3.Row = Depends(require_worker)
) -> sqlite3.Row:
    """For /workers/{worker_id}/...: the token must belong to that worker."""
    if worker_row["id"] != worker_id:
        raise forbidden("this token belongs to another worker")
    return worker_row


def require_admin(
    credentials: HTTPAuthorizationCredentials | None = Depends(admin_bearer),
    context: OrchestratorContext = Depends(get_context),
) -> str:
    if credentials is None or not _is_admin(context, credentials.credentials):
        raise unauthenticated("send 'Authorization: Bearer <admin token>'")
    return "admin"


def require_reviewer(
    credentials: HTTPBasicCredentials | None = Depends(reviewer_basic),
    context: OrchestratorContext = Depends(get_context),
) -> str:
    username = _reviewer_name(context, credentials)
    if username is None:
        raise unauthenticated("reviewer username or password is wrong")
    return username


@dataclass(frozen=True)
class Caller:
    """Who called an endpoint that accepts more than one credential."""

    reviewer: str | None = None
    worker_row: sqlite3.Row | None = None
    is_admin: bool = False


def require_reviewer_or_worker(
    basic: HTTPBasicCredentials | None = Depends(reviewer_basic),
    bearer: HTTPAuthorizationCredentials | None = Depends(worker_bearer),
    context: OrchestratorContext = Depends(get_context),
) -> Caller:
    username = _reviewer_name(context, basic)
    if username is not None:
        return Caller(reviewer=username)
    if bearer is not None:
        worker_row = _worker_for_token(context, bearer.credentials)
        if worker_row is not None:
            return Caller(worker_row=worker_row)
    raise unauthenticated("reviewer Basic auth or a worker bearer token is required")


def require_reviewer_or_admin(
    basic: HTTPBasicCredentials | None = Depends(reviewer_basic),
    bearer: HTTPAuthorizationCredentials | None = Depends(admin_bearer),
    context: OrchestratorContext = Depends(get_context),
) -> Caller:
    username = _reviewer_name(context, basic)
    if username is not None:
        return Caller(reviewer=username)
    if bearer is not None and _is_admin(context, bearer.credentials):
        return Caller(is_admin=True)
    raise unauthenticated("reviewer Basic auth or the admin token is required")


def require_webhook_secret(
    secret: str | None = Depends(webhook_secret),
    context: OrchestratorContext = Depends(get_context),
) -> None:
    expected = context.settings.webhook_secret.get_secret_value()
    if secret is None or not secrets_match(secret, expected):
        raise unauthenticated(f"wrong or missing {WEBHOOK_SECRET_HEADER}")
