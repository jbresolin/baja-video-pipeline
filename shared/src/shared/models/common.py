"""Small building blocks used by many models: field types, time, errors."""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field

# A SHA-256 written as 64 lowercase hex characters, as hashlib.hexdigest() does.
Sha256Hex = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]

# File sizes are always whole bytes and never negative.
ByteCount = Annotated[int, Field(ge=0)]

# Positions inside a video, in seconds from the start of the raw.
Seconds = Annotated[float, Field(ge=0)]

# Baja car numbers are small positive integers painted on the car.
CarNumber = Annotated[int, Field(ge=0, le=9999)]

# Passes (passagens) are counted from 1.
PassNumber = Annotated[int, Field(ge=1, le=999)]


def utc_now() -> datetime:
    """The current time with an explicit UTC timezone (never naive)."""
    return datetime.now(UTC)


class ReceivedByServer(BaseModel):
    """Base for bodies the orchestrator receives: requests, uploads, worker reports.

    Unknown fields are refused (422), so a client's typo fails loudly.
    """

    model_config = ConfigDict(extra="forbid")


class ReadByClients(BaseModel):
    """Base for what a client reads from the orchestrator: jobs, tasks, answers.

    Unknown fields are ignored, so a worker, the studio or the review UI
    never breaks because a newer orchestrator added a field (design §14).
    """

    model_config = ConfigDict(extra="ignore")


class ErrorCode(StrEnum):
    """Machine-readable error codes. HTTP status says the family; this says why."""

    # 400 / 422
    VALIDATION_ERROR = "validation_error"
    HASH_MISMATCH = "hash_mismatch"
    UPLOAD_OFFSET_MISMATCH = "upload_offset_mismatch"
    UPLOAD_TOO_LARGE = "upload_too_large"
    MISSING_REVIEW_FIELDS = "missing_review_fields"
    # ready/reprocess/retry with a prova that is not in the provas table.
    UNKNOWN_PROVA = "unknown_prova"
    UNSUPPORTED_MESSAGE = "unsupported_message"
    # 401 / 403
    UNAUTHENTICATED = "unauthenticated"
    FORBIDDEN = "forbidden"
    ENROLLMENT_TOKEN_INVALID = "enrollment_token_invalid"
    # 404
    NOT_FOUND = "not_found"
    # 409
    ILLEGAL_TRANSITION = "illegal_transition"
    ALREADY_CLAIMED = "already_claimed"
    NOT_CLAIMABLE = "not_claimable"
    LEASE_LOST = "lease_lost"
    VERSION_CONFLICT = "version_conflict"
    NO_HOLDER_ONLINE = "no_holder_online"
    # Admin pages: a user, prova, contact... with that name or key exists.
    ALREADY_EXISTS = "already_exists"
    # Admin pages: the change is not possible right now (e.g. disabling the
    # last admin, revoking a token that was already used).
    CONFLICT = "conflict"
    # restore after the discard grace period, and no copy of the raw is left.
    RAW_UNAVAILABLE = "raw_unavailable"
    # 413 / 507
    INBOX_FULL = "inbox_full"
    # 429 (POST /enroll, per source IP)
    RATE_LIMITED = "rate_limited"
    # 502 / 503
    UPSTREAM_ERROR = "upstream_error"
    DEV_MODE_DISABLED = "dev_mode_disabled"


class ErrorBody(ReadByClients):
    """Read by clients: unknown fields are ignored."""

    code: ErrorCode
    # One sentence for humans (logs, the review UI). Not meant for parsing.
    message: str
    # Extra machine-readable facts, e.g. {"expected_offset": 1048576}.
    details: dict[str, Any] = Field(default_factory=dict)


class ErrorResponse(ReadByClients):
    """The shape of every non-2xx response body: {"error": {...}}.

    Read by clients: unknown fields are ignored.
    """

    error: ErrorBody
