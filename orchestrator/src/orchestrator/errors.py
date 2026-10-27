"""Errors that become the API's standard error answer (docs/api.md §3).

Domain code raises ApiError (usually through one of the small helpers
below); the web layer turns it into

    HTTP <status>  {"error": {"code": ..., "message": ..., "details": {...}}}

so every endpoint fails the same way and clients branch on `code`.
"""

from typing import Any

from shared.models import ErrorCode, JobState
from shared.state_machine import IllegalTransitionError


class ApiError(Exception):
    def __init__(
        self,
        status_code: int,
        code: ErrorCode,
        message: str,
        details: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details or {}
        self.headers = headers or {}


def not_found(what: str) -> ApiError:
    return ApiError(404, ErrorCode.NOT_FOUND, f"no such {what}")


def forbidden(message: str) -> ApiError:
    return ApiError(403, ErrorCode.FORBIDDEN, message)


def unauthenticated(message: str = "missing or wrong credentials") -> ApiError:
    return ApiError(401, ErrorCode.UNAUTHENTICATED, message)


def validation_error(message: str, **details: Any) -> ApiError:
    return ApiError(400, ErrorCode.VALIDATION_ERROR, message, details)


def lease_lost(lease_id: str) -> ApiError:
    return ApiError(
        409,
        ErrorCode.LEASE_LOST,
        "the lease expired or was revoked; stop this task",
        {"lease_id": lease_id},
    )


def not_claimable(message: str) -> ApiError:
    return ApiError(409, ErrorCode.NOT_CLAIMABLE, message)


def illegal_transition(current: JobState, requested: JobState | str) -> ApiError:
    return ApiError(
        409,
        ErrorCode.ILLEGAL_TRANSITION,
        f"job is '{current}', which does not allow '{requested}'",
        {"current": str(current), "requested": str(requested)},
    )


def from_illegal_transition(error: IllegalTransitionError) -> ApiError:
    return illegal_transition(error.current, error.requested)


def hash_mismatch(expected: str, actual: str) -> ApiError:
    return ApiError(
        422,
        ErrorCode.HASH_MISMATCH,
        "SHA-256 does not match the expected value",
        {"expected": expected, "actual": actual},
    )


def upstream_error(service: str, message: str) -> ApiError:
    return ApiError(503, ErrorCode.UPSTREAM_ERROR, f"{service}: {message}", {"service": service})
