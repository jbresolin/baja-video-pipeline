"""The credentials (docs/api.md §2) as FastAPI dependencies.

    workerBearer   per-worker token from /enroll           → the worker's DB row
    adminBearer    BVP_ADMIN_TOKEN (scripts)                → AdminActor(None)
    sessionBearer  session token from POST /auth/login      → the signed-in user
    sessionCookie  the same token in the bvp_session cookie  (only GET /auth/media)
    webhookSecret  X-Webhook-Secret = BVP_WEBHOOK_SECRET

All three bearer kinds use `Authorization: Bearer …`; each dependency
tries the kinds it accepts. Roles: review routes accept a session of a
`reviewer` or an `admin`; admin routes accept an `admin` session or the
admin token, and answer 403 to a reviewer.

The security objects are declared with the same scheme names as the
contract, so the service's own OpenAPI document matches orchestrator/openapi.yaml.
"""

import sqlite3
from dataclasses import dataclass

from fastapi import Depends, Request
from fastapi.security import APIKeyCookie, APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer

from orchestrator.context import OrchestratorContext
from orchestrator.domain.audit import AdminActor
from orchestrator.domain.sessions import (
    SESSION_COOKIE_NAME,
    SessionUser,
    session_user_for_token,
)
from orchestrator.errors import forbidden, unauthenticated
from orchestrator.repositories import workers as workers_repo
from orchestrator.security import secrets_match, token_sha256
from shared.protocol import WEBHOOK_SECRET_HEADER

worker_bearer = HTTPBearer(scheme_name="workerBearer", auto_error=False)
admin_bearer = HTTPBearer(scheme_name="adminBearer", auto_error=False)
session_bearer = HTTPBearer(scheme_name="sessionBearer", auto_error=False)
session_cookie = APIKeyCookie(
    name=SESSION_COOKIE_NAME, scheme_name="sessionCookie", auto_error=False
)
webhook_secret = APIKeyHeader(
    name=WEBHOOK_SECRET_HEADER, scheme_name="webhookSecret", auto_error=False
)


def get_context(request: Request) -> OrchestratorContext:
    return request.app.state.context


def _worker_for_token(context: OrchestratorContext, token: str) -> sqlite3.Row | None:
    """The worker with this bearer token, unless an admin revoked it."""
    with context.database.transaction() as connection:
        row = workers_repo.find_worker_by_token_hash(connection, token_sha256(token))
    if row is None or row["revoked_at"] is not None:
        return None
    return row


def _is_admin_token(context: OrchestratorContext, token: str) -> bool:
    return secrets_match(token, context.settings.admin_token.get_secret_value())


def _session_user(
    context: OrchestratorContext, credentials: HTTPAuthorizationCredentials | None
) -> SessionUser | None:
    if credentials is None:
        return None
    return session_user_for_token(context, credentials.credentials)


def require_worker(
    credentials: HTTPAuthorizationCredentials | None = Depends(worker_bearer),
    context: OrchestratorContext = Depends(get_context),
) -> sqlite3.Row:
    if credentials is None:
        raise unauthenticated("send 'Authorization: Bearer <worker token>'")
    worker_row = _worker_for_token(context, credentials.credentials)
    if worker_row is None:
        raise unauthenticated("unknown or revoked worker token")
    return worker_row


def require_worker_matching_path(
    worker_id: str, worker_row: sqlite3.Row = Depends(require_worker)
) -> sqlite3.Row:
    """For /workers/{worker_id}/...: the token must belong to that worker."""
    if worker_row["id"] != worker_id:
        raise forbidden("this token belongs to another worker")
    return worker_row


def require_session(
    credentials: HTTPAuthorizationCredentials | None = Depends(session_bearer),
    context: OrchestratorContext = Depends(get_context),
) -> SessionUser:
    """Any signed-in user (reviewer or admin)."""
    user = _session_user(context, credentials)
    if user is None:
        raise unauthenticated("sign in again: the session is unknown, ended or expired")
    return user


def require_reviewer(user: SessionUser = Depends(require_session)) -> str:
    """Review routes: a reviewer or an admin session. Returns the username."""
    return user.username


def require_admin(
    admin: HTTPAuthorizationCredentials | None = Depends(admin_bearer),
    session: HTTPAuthorizationCredentials | None = Depends(session_bearer),
    context: OrchestratorContext = Depends(get_context),
) -> AdminActor:
    """Admin routes: the admin token (scripts) or a session of a user with role admin."""
    if admin is not None and _is_admin_token(context, admin.credentials):
        return AdminActor(username=None)
    user = _session_user(context, session)
    if user is None:
        raise unauthenticated("send the admin token or sign in as an admin")
    if not user.is_admin:
        raise forbidden(f"'{user.username}' is a reviewer; this needs an admin")
    return AdminActor(username=user.username)


@dataclass(frozen=True)
class Caller:
    """Who called an endpoint that accepts more than one credential."""

    reviewer: str | None = None
    worker_row: sqlite3.Row | None = None
    is_admin: bool = False


def require_reviewer_or_worker(
    session: HTTPAuthorizationCredentials | None = Depends(session_bearer),
    bearer: HTTPAuthorizationCredentials | None = Depends(worker_bearer),
    context: OrchestratorContext = Depends(get_context),
) -> Caller:
    user = _session_user(context, session)
    if user is not None:
        return Caller(reviewer=user.username)
    if bearer is not None:
        worker_row = _worker_for_token(context, bearer.credentials)
        if worker_row is not None:
            return Caller(worker_row=worker_row)
    raise unauthenticated("a signed-in reviewer or a worker bearer token is required")


def require_reviewer_or_admin(
    session: HTTPAuthorizationCredentials | None = Depends(session_bearer),
    bearer: HTTPAuthorizationCredentials | None = Depends(admin_bearer),
    context: OrchestratorContext = Depends(get_context),
) -> Caller:
    if bearer is not None and _is_admin_token(context, bearer.credentials):
        return Caller(is_admin=True)
    user = _session_user(context, session)
    if user is not None:
        return Caller(reviewer=user.username, is_admin=user.is_admin)
    raise unauthenticated("a signed-in reviewer or the admin token is required")


def require_webhook_secret(
    secret: str | None = Depends(webhook_secret),
    context: OrchestratorContext = Depends(get_context),
) -> None:
    expected = context.settings.webhook_secret.get_secret_value()
    if secret is None or not secrets_match(secret, expected):
        raise unauthenticated(f"wrong or missing {WEBHOOK_SECRET_HEADER}")
