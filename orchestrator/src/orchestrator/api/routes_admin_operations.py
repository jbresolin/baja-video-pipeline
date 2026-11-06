"""Routes: admin Enrollment, Workers and Settings pages, and the audit of admin changes."""

from fastapi import APIRouter, Depends, Query

from orchestrator.api.auth import get_context, require_admin
from orchestrator.context import OrchestratorContext
from orchestrator.domain import event_log, results_sync, settings_page, workers_admin
from orchestrator.domain.audit import AdminActor
from shared.models import (
    AdminArea,
    EnrollmentTokenInfo,
    EnrollmentTokensResponse,
    EventsResponse,
    ResultsSyncReport,
    SettingsResponse,
    UpdateSettingsRequest,
    UpdateWorkerRequest,
    Worker,
)

router = APIRouter()


@router.get(
    "/admin/enrollment-tokens",
    tags=["enrollment"],
    response_model=EnrollmentTokensResponse,
    dependencies=[Depends(require_admin)],
)
def list_enrollment_tokens(
    context: OrchestratorContext = Depends(get_context),
) -> EnrollmentTokensResponse:
    return workers_admin.list_enrollment_tokens(context)


@router.post(
    "/admin/enrollment-tokens/{token_id}/revoke",
    tags=["enrollment"],
    response_model=EnrollmentTokenInfo,
)
def revoke_enrollment_token(
    token_id: str,
    actor: AdminActor = Depends(require_admin),
    context: OrchestratorContext = Depends(get_context),
) -> EnrollmentTokenInfo:
    return workers_admin.revoke_enrollment_token(context, actor, token_id)


@router.patch("/admin/workers/{worker_id}", tags=["admin"], response_model=Worker)
def rename_worker(
    worker_id: str,
    body: UpdateWorkerRequest,
    actor: AdminActor = Depends(require_admin),
    context: OrchestratorContext = Depends(get_context),
) -> Worker:
    return workers_admin.rename_worker(context, actor, worker_id, body)


@router.post("/admin/workers/{worker_id}/revoke", tags=["admin"], response_model=Worker)
def revoke_worker(
    worker_id: str,
    actor: AdminActor = Depends(require_admin),
    context: OrchestratorContext = Depends(get_context),
) -> Worker:
    return workers_admin.revoke_worker(context, actor, worker_id)


@router.get(
    "/admin/settings",
    tags=["admin"],
    response_model=SettingsResponse,
    dependencies=[Depends(require_admin)],
)
def get_settings(context: OrchestratorContext = Depends(get_context)) -> SettingsResponse:
    return settings_page.settings_page(context)


@router.patch("/admin/settings", tags=["admin"], response_model=SettingsResponse)
def update_settings(
    body: UpdateSettingsRequest,
    actor: AdminActor = Depends(require_admin),
    context: OrchestratorContext = Depends(get_context),
) -> SettingsResponse:
    return settings_page.update_settings(context, actor, body)


@router.post("/admin/results-sync", tags=["admin"], response_model=ResultsSyncReport)
def sync_from_results_api(
    actor: AdminActor = Depends(require_admin),
    context: OrchestratorContext = Depends(get_context),
) -> ResultsSyncReport:
    return results_sync.sync_or_raise(context, actor)


@router.get(
    "/admin/audit",
    tags=["admin"],
    response_model=EventsResponse,
    dependencies=[Depends(require_admin)],
)
def admin_audit(
    area: AdminArea | None = Query(None),
    limit: int = Query(20, ge=1, le=500),
    context: OrchestratorContext = Depends(get_context),
) -> EventsResponse:
    return event_log.recent_admin_changes(context, area, limit)
