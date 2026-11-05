"""Admin Users page: list, add, change role, disable, reset a password.

Users are never deleted, only disabled, so `Job.reviewed_by` and the
event log keep pointing at a known account. Two rules protect access:
the last enabled admin cannot be disabled or demoted, and disabling a
user or changing their password ends their open sessions.
"""

import sqlite3
from typing import Any

from orchestrator.context import OrchestratorContext
from orchestrator.domain.audit import AdminActor, record_admin_change
from orchestrator.errors import ApiError, not_found
from orchestrator.repositories import users as users_repo
from orchestrator.security import hash_password
from shared.models import (
    CreateUserRequest,
    ErrorCode,
    UpdateUserRequest,
    User,
    UserRole,
    UsersResponse,
)


def _audit_view(user: User) -> dict[str, Any]:
    """What the audit log keeps of a user: never the password hash."""
    return user.model_dump(mode="json", include={"username", "role", "disabled"})


def list_users(context: OrchestratorContext) -> UsersResponse:
    with context.database.transaction() as connection:
        rows = users_repo.list_user_rows(connection)
    return UsersResponse(users=[users_repo.user_from_row(row) for row in rows])


def create_user(
    context: OrchestratorContext, actor: AdminActor, request: CreateUserRequest
) -> User:
    # bcrypt before the transaction: it is slow and must not hold the DB lock.
    password_hash = hash_password(request.password, context.settings.password_hash_rounds)
    now = context.clock.now()
    with context.database.transaction() as connection:
        if users_repo.get_user_row(connection, request.username) is not None:
            raise ApiError(
                409, ErrorCode.ALREADY_EXISTS, f"user '{request.username}' already exists"
            )
        users_repo.insert_user(connection, request.username, password_hash, request.role, now)
        user = users_repo.user_from_row(users_repo.get_user_row(connection, request.username))
        record_admin_change(
            connection,
            now,
            actor,
            "user_created",
            f"{request.role} '{request.username}' created",
            after=_audit_view(user),
        )
    return user


def _check_keeps_an_admin(
    connection: sqlite3.Connection, row: sqlite3.Row, request: UpdateUserRequest
) -> None:
    """Refuse a change that would leave nobody able to open the admin pages."""
    is_enabled_admin = row["role"] == UserRole.ADMIN and not row["disabled"]
    loses_admin = request.role == UserRole.REVIEWER or request.disabled is True
    if is_enabled_admin and loses_admin and users_repo.count_enabled_admins(connection) <= 1:
        raise ApiError(
            409,
            ErrorCode.CONFLICT,
            "this is the last enabled admin; make another user admin first",
        )


def update_user(
    context: OrchestratorContext, actor: AdminActor, username: str, request: UpdateUserRequest
) -> User:
    new_hash = None
    if request.password is not None:
        new_hash = hash_password(request.password, context.settings.password_hash_rounds)
    now = context.clock.now()
    with context.database.transaction() as connection:
        row = users_repo.get_user_row(connection, username)
        if row is None:
            raise not_found("user")
        _check_keeps_an_admin(connection, row, request)
        before = users_repo.user_from_row(row)
        changes: dict[str, Any] = {}
        if request.role is not None:
            changes["role"] = str(request.role)
        if request.disabled is not None:
            changes["disabled"] = int(request.disabled)
        if new_hash is not None:
            changes["password_hash"] = new_hash
        if changes:
            users_repo.update_user(connection, username, now, changes)
        # A disabled user, or one whose password changed, must sign in again.
        ended_sessions = 0
        if request.disabled or new_hash is not None:
            ended_sessions = users_repo.delete_sessions_of_user(connection, username)
        after = users_repo.user_from_row(users_repo.get_user_row(connection, username))
        what = []
        if request.role is not None and request.role != before.role:
            what.append(f"role {before.role} → {after.role}")
        if request.disabled is not None and request.disabled != before.disabled:
            what.append("disabled" if request.disabled else "enabled")
        if new_hash is not None:
            what.append("password changed")
        if ended_sessions:
            what.append(f"{ended_sessions} session(s) ended")
        record_admin_change(
            connection,
            now,
            actor,
            "user_updated",
            f"user '{username}': {', '.join(what) or 'no change'}",
            before=_audit_view(before),
            after=_audit_view(after) | {"password_changed": new_hash is not None},
        )
    return after
