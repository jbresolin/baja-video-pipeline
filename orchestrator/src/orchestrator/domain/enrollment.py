"""Enrollment: admin issues a one-time token; the worker exchanges it at POST /enroll.

The exchange mints a Tailscale auth key first and spends the token only
after that succeeded (infra/tailnet/mint_auth_key.md), so a Tailscale
outage does not burn a volunteer's token.
"""

from datetime import timedelta

from orchestrator.clients.tailscale import TailscaleError
from orchestrator.context import OrchestratorContext
from orchestrator.errors import ApiError, upstream_error
from orchestrator.repositories import events as events_repo
from orchestrator.repositories import workers as workers_repo
from orchestrator.security import new_id, new_secret_token, token_sha256
from shared.models import (
    CreateEnrollmentTokenRequest,
    EnrollmentToken,
    EnrollmentTokenCreated,
    EnrollRequest,
    EnrollResponse,
    ErrorCode,
)
from shared.protocol import HEARTBEAT_INTERVAL_SECONDS, LEASE_TTL_SECONDS


def create_enrollment_token(
    context: OrchestratorContext, request: CreateEnrollmentTokenRequest
) -> EnrollmentTokenCreated:
    now = context.clock.now()
    token = new_secret_token()
    record = EnrollmentToken(
        token_sha256=token_sha256(token),
        volunteer_name=request.volunteer_name,
        role=request.role,
        created_at=now,
        expires_at=now + timedelta(hours=request.valid_for_hours),
    )
    with context.database.transaction() as connection:
        workers_repo.insert_enrollment_token(connection, record)
        events_repo.record_event(
            connection,
            now,
            actor="admin",
            kind="enrollment_token_created",
            reason=f"{request.role} token for {request.volunteer_name}",
        )
    return EnrollmentTokenCreated(
        token=token,
        volunteer_name=record.volunteer_name,
        role=record.role,
        expires_at=record.expires_at,
    )


def invalid_token_error() -> ApiError:
    # One answer for unknown, used and expired tokens: the caller learns nothing.
    return ApiError(
        401, ErrorCode.ENROLLMENT_TOKEN_INVALID, "enrollment token is unknown, used or expired"
    )


def tailscale_key_description(role: str, token_hash: str) -> str:
    """ "baja worker enr-1a2b3c4d5e6f": ties the key to the token (letters, digits, -, space)."""
    return f"baja {role} enr-{token_hash[:12]}"


def enroll_worker(context: OrchestratorContext, request: EnrollRequest) -> EnrollResponse:
    hashed = token_sha256(request.enrollment_token)
    with context.database.transaction() as connection:
        token = workers_repo.get_enrollment_token(connection, hashed)
    now = context.clock.now()
    if token is None or token.used_at is not None or token.expires_at <= now:
        raise invalid_token_error()

    # Network call outside any DB transaction.
    try:
        minted = context.tailscale.mint_auth_key(
            token.role, tailscale_key_description(str(token.role), hashed)
        )
    except TailscaleError as error:
        raise upstream_error("tailscale", str(error)) from error

    worker_id = new_id()
    bearer_token = new_secret_token()
    now = context.clock.now()
    with context.database.transaction() as connection:
        if not workers_repo.mark_enrollment_token_used(
            connection, hashed, worker_id, minted.key_id, now
        ):
            # Someone used the same token a moment ago (double click).
            raise invalid_token_error()
        workers_repo.insert_worker(
            connection,
            worker_id=worker_id,
            name=request.worker_name,
            role=token.role,
            hostname=request.hostname,
            capabilities=request.capabilities,
            bearer_token_sha256=token_sha256(bearer_token),
            storage_cap_bytes=request.storage_cap_bytes,
            now=now,
        )
        events_repo.record_event(
            connection,
            now,
            actor=f"worker:{worker_id}",
            kind="worker_enrolled",
            worker_id=worker_id,
            reason=(
                f"{token.role} '{request.worker_name}' for {token.volunteer_name},"
                f" tailscale key {minted.key_id}"
            ),
        )
    return EnrollResponse(
        worker_id=worker_id,
        role=token.role,
        bearer_token=bearer_token,
        tailscale_auth_key=minted.key,
        tailscale_tags=minted.tags,
        heartbeat_interval_s=HEARTBEAT_INTERVAL_SECONDS,
        lease_ttl_s=LEASE_TTL_SECONDS,
    )
