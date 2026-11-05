"""Contract: admin Enrollment, Workers and Settings pages, and the admin audit (LIVESTACK-37).

POST /admin/enrollment-tokens (issuing a token) is in routes_intake_enrollment.
"""

from fastapi import APIRouter, Query

from shared.models import (
    AdminArea,
    EnrollmentTokenInfo,
    EnrollmentTokensResponse,
    EventsResponse,
    SettingsResponse,
    UpdateSettingsRequest,
    Worker,
)
from tools.openapi_contract.auth import error_responses, not_implemented
from tools.openapi_contract.routes_admin_people import ADMIN_ONLY

router = APIRouter()


@router.get(
    "/admin/enrollment-tokens",
    tags=["enrollment"],
    summary="Every enrollment token: unused, used (by which worker), expired or revoked",
    response_model=EnrollmentTokensResponse,
    dependencies=ADMIN_ONLY,
    responses=error_responses(401, 403),
)
def list_enrollment_tokens() -> EnrollmentTokensResponse:
    not_implemented()


@router.post(
    "/admin/enrollment-tokens/{token_id}/revoke",
    tags=["enrollment"],
    summary="Revoke a token nobody used yet",
    response_model=EnrollmentTokenInfo,
    dependencies=ADMIN_ONLY,
    responses=error_responses(401, 403, 404, 409),
)
def revoke_enrollment_token(token_id: str) -> EnrollmentTokenInfo:
    """A revoked token gets 401 `enrollment_token_invalid` at /enroll. A used
    token → 409 `conflict` (revoke the worker instead). Revoking twice is
    harmless."""
    not_implemented()


@router.post(
    "/admin/workers/{worker_id}/revoke",
    tags=["admin"],
    summary="Revoke a worker: its bearer token stops working",
    response_model=Worker,
    dependencies=ADMIN_ONLY,
    responses=error_responses(401, 403, 404),
)
def revoke_worker(worker_id: str) -> Worker:
    """Every call with the worker's token then answers 401. Its leases end
    (their tasks go to other workers) and it stops being a holder of any
    raw. Removing the device from the tailnet stays manual (Tailscale admin
    console). Revoking twice is harmless."""
    not_implemented()


@router.get(
    "/admin/settings",
    tags=["admin"],
    summary="Editable settings, plus Evolution state, secrets configured/missing, disk use",
    response_model=SettingsResponse,
    dependencies=ADMIN_ONLY,
    responses=error_responses(401, 403),
)
def get_settings() -> SettingsResponse:
    """Secrets (Evolution key, Tailscale OAuth secret, webhook secret, admin
    token) stay in .env and are never sent: only "configured" or "missing".
    The settings' .env values are first-start defaults only."""
    not_implemented()


@router.patch(
    "/admin/settings",
    tags=["admin"],
    summary="Change intake mode, inbox soft cap, proxy eviction threshold or installer URL",
    response_model=SettingsResponse,
    dependencies=ADMIN_ONLY,
    responses=error_responses(400, 401, 403),
)
def update_settings(body: UpdateSettingsRequest) -> SettingsResponse:
    """`inbox_hold_gb` must not exceed BVP_INBOX_CAP_GB (400)."""
    not_implemented()


@router.get(
    "/admin/audit",
    tags=["admin"],
    summary="The newest admin changes (newest first), optionally of one admin page",
    response_model=EventsResponse,
    dependencies=ADMIN_ONLY,
    responses=error_responses(400, 401, 403),
)
def admin_audit(
    area: AdminArea | None = Query(None),
    limit: int = Query(20, ge=1, le=500),
) -> EventsResponse:
    """Events whose actor is "admin" (the token) or "admin:<username>"; each
    carries `details: {before, after}`. Unlike GET /events: newest first."""
    not_implemented()
