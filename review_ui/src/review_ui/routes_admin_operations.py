"""Admin pages: Cadastro de PCs (enrollment tokens), Workers and Configurações (settings)."""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, Response

from review_ui.admin_view import (
    INTAKE_MODE_LABELS,
    TOKEN_STATUS_LABELS,
    WORKER_ROLE_LABELS,
    admin_context,
    apply_change,
    audit_lines,
    form_text,
    orchestrator_error_text,
)
from review_ui.catalog import text
from review_ui.forms import read_form
from review_ui.orchestrator_client import OrchestratorError
from review_ui.request_context import admin_client_for, render_page
from shared.models import (
    AdminArea,
    CreateEnrollmentTokenRequest,
    UpdateSettingsRequest,
    WorkerRole,
)

router = APIRouter(prefix="/review/admin")

ENROLLMENT_PATH = "/review/admin/enrollment"
WORKERS_PATH = "/review/admin/workers"
SETTINGS_PATH = "/review/admin/settings"

# The validity choices offered for a new token, in hours.
VALIDITY_CHOICES = [(hours, text(f"enrollment.validity.{hours}")) for hours in (24, 72, 168, 720)]


# --- Cadastro de PCs (enrollment tokens) ---


def _enrollment_page(
    request: Request, error: str | None = None, status_code: int = 200, created=None
):
    admin = admin_client_for(request)
    context = admin_context(
        request,
        "enrollment",
        tokens=admin.enrollment_tokens().tokens,
        # Shown once, right after it was issued; never stored by this UI.
        created=created,
        roles=[(role.value, WORKER_ROLE_LABELS[role.value]) for role in WorkerRole],
        validity_choices=VALIDITY_CHOICES,
        status_labels=TOKEN_STATUS_LABELS,
        role_labels=WORKER_ROLE_LABELS,
        audit=audit_lines(admin.audit(AdminArea.ENROLLMENT)),
    )
    context["error"] = error
    return render_page(request, "admin/enrollment.html", context, status_code=status_code)


@router.get("/enrollment", response_class=HTMLResponse)
def enrollment_page(request: Request) -> HTMLResponse:
    return _enrollment_page(request)


@router.post("/enrollment/create")
def create_token(request: Request, form: dict = Depends(read_form)) -> Response:
    """Issue a token and show it on this response only (no redirect, no URL, no storage)."""
    try:
        body = CreateEnrollmentTokenRequest(
            volunteer_name=form_text(form, "volunteer_name") or "",
            role=form.get("role", "worker"),
            valid_for_hours=int(form.get("valid_for_hours", "72")),
        )
    except ValueError:
        return _enrollment_page(request, text("enrollment.name_required"), 400)
    try:
        created = admin_client_for(request).create_enrollment_token(body)
    except OrchestratorError as error:
        if error.status_code in (401, 403):
            raise
        return _enrollment_page(request, orchestrator_error_text(error), error.status_code)
    response = _enrollment_page(request, created=created)
    # The token is in this page: keep it out of the browser cache and history.
    response.headers["Cache-Control"] = "no-store"
    return response


@router.post("/enrollment/{token_id}/revoke")
def revoke_token(request: Request, token_id: str, form: dict = Depends(read_form)) -> Response:
    return apply_change(
        lambda: admin_client_for(request).revoke_enrollment_token(token_id),
        ENROLLMENT_PATH,
        "token_revoked",
        lambda error, status: _enrollment_page(request, error, status),
    )


# --- Workers ---


def _workers_page(request: Request, error: str | None = None, status_code: int = 200):
    admin = admin_client_for(request)
    # Reuses the fleet data (GET /fleet): who is online, what they hold.
    context = admin_context(
        request,
        "workers",
        fleet=admin.fleet(),
        role_labels=WORKER_ROLE_LABELS,
        audit=audit_lines(admin.audit(AdminArea.WORKERS)),
    )
    context["error"] = error
    return render_page(request, "admin/workers.html", context, status_code=status_code)


@router.get("/workers", response_class=HTMLResponse)
def workers_page(request: Request) -> HTMLResponse:
    return _workers_page(request)


@router.post("/workers/{worker_id}/revoke")
def revoke_worker(request: Request, worker_id: str, form: dict = Depends(read_form)) -> Response:
    return apply_change(
        lambda: admin_client_for(request).revoke_worker(worker_id),
        WORKERS_PATH,
        "worker_revoked",
        lambda error, status: _workers_page(request, error, status),
    )


# --- Configurações (settings) ---


def _settings_page(request: Request, error: str | None = None, status_code: int = 200):
    admin = admin_client_for(request)
    page = admin.settings()
    context = admin_context(
        request,
        "settings",
        values=page.settings,
        status=page.status,
        intake_modes=INTAKE_MODE_LABELS,
        audit=audit_lines(admin.audit(AdminArea.SETTINGS)),
    )
    context["error"] = error
    return render_page(request, "admin/settings.html", context, status_code=status_code)


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request) -> HTMLResponse:
    return _settings_page(request)


def _number(form: dict, name: str) -> float:
    """A number typed in the form; a decimal comma is fine. ValueError otherwise."""
    return float(form.get(name, "").replace(",", "."))


def _optional_number(form: dict, name: str) -> float | None:
    """Like _number, but a field left out or blank keeps its stored value (None)."""
    return _number(form, name) if form.get(name, "").strip() else None


@router.post("/settings")
def save_settings(request: Request, form: dict = Depends(read_form)) -> Response:
    try:
        body = UpdateSettingsRequest(
            intake_mode=form.get("intake_mode"),
            inbox_hold_gb=_number(form, "inbox_hold_gb"),
            proxy_evict_min_free_gb=_number(form, "proxy_evict_min_free_gb"),
            installer_download_url=form.get("installer_download_url", "").strip(),
            event_id=form.get("event_id", "").strip(),
            results_api_base_url=form_text(form, "results_api_base_url"),
            discard_grace_hours=_optional_number(form, "discard_grace_hours"),
            outbox_keep_unarchived_hours=_optional_number(form, "outbox_keep_unarchived_hours"),
        )
    except ValueError:
        return _settings_page(
            request,
            text("settings.invalid"),
            400,
        )
    return apply_change(
        lambda: admin_client_for(request).update_settings(body),
        SETTINGS_PATH,
        "settings_updated",
        lambda error, status: _settings_page(request, error, status),
    )
