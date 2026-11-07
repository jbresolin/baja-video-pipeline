"""Contract: admin Enrollment, Workers and Settings pages, and the admin audit (LIVESTACK-37).

POST /admin/enrollment-tokens (issuing a token) is in routes_intake_enrollment.
"""

from fastapi import APIRouter, Query

from shared.models import (
    AdminArea,
    EnrollmentTokenInfo,
    EnrollmentTokensResponse,
    EventsResponse,
    PassResultsSettings,
    PassResultTestRequest,
    PassResultTestResponse,
    ResultsSyncReport,
    SettingsResponse,
    UpdatePassResultsConfigRequest,
    UpdateSettingsRequest,
    UpdateWorkerRequest,
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


@router.patch(
    "/admin/workers/{worker_id}",
    tags=["admin"],
    summary="Set a worker's display name (shown everywhere instead of the host name)",
    response_model=Worker,
    dependencies=ADMIN_ONLY,
    responses=error_responses(400, 401, 403, 404),
)
def rename_worker(worker_id: str, body: UpdateWorkerRequest) -> Worker:
    """`Worker.name` is the display name: every page, event line and the
    fleet show it, with `Worker.hostname` (as reported at enrollment) as
    secondary text. Revoked workers can be renamed too. Recorded in the
    audit as `worker_renamed`."""
    not_implemented()


@router.post(
    "/admin/workers/{worker_id}/make-master",
    tags=["admin"],
    summary="Make this worker the master; the current master becomes a worker",
    response_model=Worker,
    dependencies=ADMIN_ONLY,
    responses=error_responses(401, 403, 404, 409),
)
def make_master(worker_id: str) -> Worker:
    """One master at a time: in one step the worker becomes `master` and the
    current master `worker` (its `archive_clip` leases end). Both PCs learn
    it from their next heartbeat answer (`HeartbeatResponse.role`), save it
    and restart; no config edit. Only between master and worker: a studio
    (other tailnet tag, needs a new enrollment) or a revoked worker → 409
    `conflict`. Promoting the master again changes nothing. Audited as
    `master_changed`."""
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
    """`inbox_hold_gb` must not exceed BVP_INBOX_CAP_GB (400). A new
    `event_id` (or API address) syncs provas and teams at once; a failed
    sync does not undo the change (see `status.results_sync`)."""
    not_implemented()


@router.post(
    "/admin/results-sync",
    tags=["admin"],
    summary="Fetch the configured event's provas and teams from the Baja results API now",
    response_model=ResultsSyncReport,
    dependencies=ADMIN_ONLY,
    responses=error_responses(400, 401, 403, 422, 503),
)
def sync_from_results_api() -> ResultsSyncReport:
    """Also runs by itself when the event id changes and every 6 h while one
    is set. Provas are keyed by the API's prova id, teams by car number; a
    sync adds and renames, and marks rows the API stopped listing inactive
    (never deletes them). Admin corrections of synced teams are kept.

    No event id set → 400. Event id unknown to the API → 422 (details: the
    report). API unreachable or rate-limited → 503 `upstream_error`. The
    report is kept either way (GET /admin/settings, `status.results_sync`).
    """
    not_implemented()


@router.get(
    "/admin/pass-results",
    tags=["admin"],
    summary="The pass-results configuration and how fetching each prova's results goes",
    response_model=PassResultsSettings,
    dependencies=ADMIN_ONLY,
    responses=error_responses(401, 403),
)
def get_pass_results() -> PassResultsSettings:
    """LIVESTACK-47. `config_text` exactly as saved ("" = none). One entry per
    configured prova: our prova with that results API id (null when none was
    synced), its table ("26BR_MAN"), whether jobs need it now (then it is
    fetched every 30 s, every 5 min once the event is finished) and the
    last fetch: when, whether it worked, the error, since when it fails."""
    not_implemented()


@router.put(
    "/admin/pass-results/config",
    tags=["admin"],
    summary="Replace the pass-results configuration (a JSON text, checked before it is stored)",
    response_model=PassResultsSettings,
    dependencies=ADMIN_ONLY,
    responses=error_responses(400, 401, 403, 422),
)
def update_pass_results_config(body: UpdatePassResultsConfigRequest) -> PassResultsSettings:
    """A configuration that is not valid answers 422 `validation_error` with
    `details` = {`path` (e.g. "provas.MAN.result.decimals"), `problem` (one of
    not_json, wrong_type, unknown_key, missing_key, empty_text, too_long,
    out_of_range, not_one_of, bad_id, bad_name, nothing_to_show), and the
    problem's own values (`line`/`column` for not_json, `expected`,
    `minimum`/`maximum`, `choices`, `allowed`, `max_length`)}; the one in force
    stays. Audited as `pass_results_config_updated`. Shape: docs/design.md §32."""
    not_implemented()


@router.post(
    "/admin/pass-results/test",
    tags=["admin"],
    summary='"Testar": the result line for one prova, car and passagem, from live data',
    response_model=PassResultTestResponse,
    dependencies=ADMIN_ONLY,
    responses=error_responses(400, 401, 403, 422),
)
def test_pass_result(body: PassResultTestRequest) -> PassResultTestResponse:
    """Fetches the prova's table now (an answer from the last 5 s is reused)
    and builds the text exactly as a render would, with the car's row as the
    API sent it. `config_text`, when given, is tried instead of the saved
    configuration (422 as for PUT …/config when it is not valid)."""
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
