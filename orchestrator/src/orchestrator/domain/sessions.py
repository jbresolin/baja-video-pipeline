"""Sign-in for the review UI: POST /auth/login, /auth/logout, GET /auth/me, /auth/media.

A successful login creates a session: a random token the review UI keeps
in an httpOnly cookie and sends back as `Authorization: Bearer <token>`.
Only the token's SHA-256 is stored. A session ends at logout, after
SESSION_IDLE_TIMEOUT without a request, or when its user is disabled or
gets a new password.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from orchestrator.context import OrchestratorContext
from orchestrator.db.database import from_db_time
from orchestrator.errors import unauthenticated
from orchestrator.repositories import users as users_repo
from orchestrator.security import (
    hash_password,
    new_secret_token,
    password_matches,
    token_sha256,
)
from shared.models import CurrentUser, LoginRequest, LoginResponse, UserRole

SESSION_IDLE_TIMEOUT = timedelta(hours=12)
# last_seen_at is written at most this often per session, so the many Range
# requests of the video player (forward_auth on /proxies/*) do not each
# write to the database.
TOUCH_INTERVAL = timedelta(minutes=1)
# The cookie the review UI sets and Caddy's forward_auth passes on (/auth/media).
SESSION_COOKIE_NAME = "bvp_session"

# bcrypt cost → a hash of a random password. Checked against when the
# username is unknown, so a wrong username takes as long as a wrong password
# (the answer must not reveal which one was wrong).
_unknown_user_hashes: dict[int, str] = {}


def _hash_for_unknown_user(rounds: int) -> str:
    if rounds not in _unknown_user_hashes:
        _unknown_user_hashes[rounds] = hash_password(new_secret_token()[:20], rounds)
    return _unknown_user_hashes[rounds]


@dataclass(frozen=True)
class SessionUser:
    """The signed-in user behind a session token."""

    username: str
    role: UserRole
    token_sha256: str

    @property
    def is_admin(self) -> bool:
        return self.role == UserRole.ADMIN

    def as_current_user(self) -> CurrentUser:
        return CurrentUser(username=self.username, role=self.role)


def login(context: OrchestratorContext, request: LoginRequest) -> LoginResponse:
    """Check the password; on success open a session. 401 for any wrong detail."""
    with context.database.transaction() as connection:
        row = users_repo.get_user_row(connection, request.username)
    # bcrypt outside the transaction: it is slow and must not hold the DB lock.
    if row is not None:
        password_hash = row["password_hash"]
    else:
        password_hash = _hash_for_unknown_user(context.settings.password_hash_rounds)
    password_ok = password_matches(request.password, password_hash)
    if row is None or not password_ok or row["disabled"]:
        raise unauthenticated("wrong username or password, or the user is disabled")

    token = new_secret_token()
    now = context.clock.now()
    with context.database.transaction() as connection:
        users_repo.insert_session(connection, token_sha256(token), row["username"], now)
    return LoginResponse(
        session_token=token,
        user=CurrentUser(username=row["username"], role=UserRole(row["role"])),
        idle_timeout_s=int(SESSION_IDLE_TIMEOUT.total_seconds()),
    )


def logout(context: OrchestratorContext, session: SessionUser) -> None:
    with context.database.transaction() as connection:
        users_repo.delete_session(connection, session.token_sha256)


def session_user_for_token(context: OrchestratorContext, token: str) -> SessionUser | None:
    """The user of a live session, or None (unknown, idle too long, or user disabled).

    Every successful check counts as activity and keeps the session alive.
    """
    hashed = token_sha256(token)
    now = context.clock.now()
    with context.database.transaction() as connection:
        row = users_repo.find_session_with_user(connection, hashed)
        if row is None:
            return None
        last_seen = from_db_time(row["last_seen_at"])
        if row["disabled"] or now - last_seen >= SESSION_IDLE_TIMEOUT:
            users_repo.delete_session(connection, hashed)
            return None
        if now - last_seen >= TOUCH_INTERVAL:
            users_repo.touch_session(connection, hashed, now)
    return SessionUser(username=row["username"], role=UserRole(row["role"]), token_sha256=hashed)


def session_token_from_cookie_header(cookie_header: str | None) -> str | None:
    """The bvp_session value from a raw `Cookie:` header (what forward_auth passes on)."""
    if not cookie_header:
        return None
    for part in cookie_header.split(";"):
        name, _, value = part.strip().partition("=")
        if name == SESSION_COOKIE_NAME and value:
            return value
    return None


def delete_idle_sessions(context: OrchestratorContext, now: datetime) -> int:
    """Housekeeping (background sweeper): drop sessions idle for longer than the timeout."""
    with context.database.transaction() as connection:
        return users_repo.delete_sessions_idle_since(connection, now - SESSION_IDLE_TIMEOUT)
