"""Sign-in and user accounts for the review UI (admin page, LIVESTACK-37).

The review UI signs a person in with POST /auth/login and keeps the
session token in a cookie; every later call carries it as
`Authorization: Bearer <session token>` (the `sessionBearer` scheme).
"""

from datetime import datetime

from pydantic import Field

from shared.models.common import ReadByClients, ReceivedByServer
from shared.models.enums import UserRole

# Letters, digits and . _ @ - (the reviewers.txt rule, so imported names fit).
USERNAME_PATTERN = r"^[A-Za-z0-9._@-]+$"
MIN_PASSWORD_LENGTH = 8
# bcrypt only looks at the first 72 bytes; refuse more so nobody is misled.
MAX_PASSWORD_BYTES = 72


class LoginRequest(ReceivedByServer):
    """Received by the orchestrator: unknown fields are refused."""

    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=200)


class CurrentUser(ReadByClients):
    """Who a session belongs to (GET /auth/me).

    Read by clients: unknown fields are ignored.
    """

    username: str
    role: UserRole


class LoginResponse(ReadByClients):
    """Read by clients: unknown fields are ignored."""

    # Send as "Authorization: Bearer <session_token>". Shown once; only its
    # SHA-256 is stored.
    session_token: str
    user: CurrentUser
    # The session ends after this many seconds without a request (12 h).
    idle_timeout_s: int


class User(ReadByClients):
    """A review UI account, as the admin Users page lists it. No password hash.

    Read by clients: unknown fields are ignored.
    """

    username: str
    role: UserRole
    # A disabled user cannot sign in, and their open sessions were ended.
    disabled: bool
    created_at: datetime
    updated_at: datetime


class UsersResponse(ReadByClients):
    """Read by clients: unknown fields are ignored."""

    # Sorted by username.
    users: list[User]


class CreateUserRequest(ReceivedByServer):
    """Received by the orchestrator: unknown fields are refused."""

    username: str = Field(min_length=1, max_length=64, pattern=USERNAME_PATTERN)
    password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=MAX_PASSWORD_BYTES)
    role: UserRole = UserRole.REVIEWER


class UpdateUserRequest(ReceivedByServer):
    """PATCH /admin/users/{username}. Fields left out keep their value.

    Received by the orchestrator: unknown fields are refused.
    """

    role: UserRole | None = None
    disabled: bool | None = None
    # A new password. The user's open sessions end, so they sign in again.
    password: str | None = Field(
        default=None, min_length=MIN_PASSWORD_LENGTH, max_length=MAX_PASSWORD_BYTES
    )
