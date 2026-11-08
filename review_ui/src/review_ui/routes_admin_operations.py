"""Admin pages: Cadastro de PCs (enrollment tokens), Workers and Configurações (settings)."""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, Response

from review_ui.admin_view import (
    INTAKE_MODE_LABELS,
    SCHOOL_NAME_STYLE_LABELS,
    TEAM_NAME_STYLE_LABELS,
    TOKEN_STATUS_LABELS,
    WORKER_ROLE_LABELS,
    admin_context,
    apply_change,
    audit_lines,
    form_text,
    orchestrator_error_text,
)
from review_ui.catalog import text
from review_ui.clips_and_fleet_view import active_workers
from review_ui.forms import read_form
from review_ui.orchestrator_client import OrchestratorError, OrchestratorUnavailableError
from review_ui.pass_results_settings_view import PassResultsForm, pass_results_block
from review_ui.request_context import admin_client_for, render_page
from shared.duplicate_video_message import MAX_DUPLICATE_VIDEO_MESSAGE_LENGTH
from shared.models import (
    MAX_PASS_RESULTS_CONFIG_LENGTH,
    AdminArea,
    CreateEnrollmentTokenRequest,
    EnrollmentTokenStatus,
    PassResultsSettings,
    UpdateSettingsRequest,
    WorkerRole,
    utc_now,
)
from shared.vertical_video_message import MAX_VERTICAL_VIDEO_MESSAGE_LENGTH
from shared.welcome_message import (
    EXAMPLE_VALUES,
    MAX_WELCOME_MESSAGE_LENGTH,
    PLACEHOLDERS,
    fill_welcome_message,
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
    tokens = admin.enrollment_tokens().tokens
    context = admin_context(
        request,
        "enrollment",
        # Still to hand out on top; used, expired and revoked ones folded below.
        unused_tokens=[token for token in tokens if token.status == EnrollmentTokenStatus.UNUSED],
        other_tokens=[token for token in tokens if token.status != EnrollmentTokenStatus.UNUSED],
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


def current_master_name(fleet) -> str | None:
    """The display name of the (non-revoked) master, if there is one."""
    for entry in active_workers(fleet):
        if entry.worker.role == WorkerRole.MASTER:
            return entry.worker.name
    return None


def _workers_page(request: Request, error: str | None = None, status_code: int = 200):
    admin = admin_client_for(request)
    # Reuses the fleet data (GET /fleet): who is online, what they hold.
    fleet = admin.fleet()
    context = admin_context(
        request,
        "workers",
        active_workers=active_workers(fleet),
        # Named in the "Tornar master" confirmation: who stops being the master.
        current_master=current_master_name(fleet),
        # Revoked ones go to a folded "Revogados" section at the bottom.
        revoked_workers=[entry for entry in fleet.workers if entry.worker.revoked_at is not None],
        role_labels=WORKER_ROLE_LABELS,
        audit=audit_lines(admin.audit(AdminArea.WORKERS)),
    )
    context["error"] = error
    return render_page(request, "admin/workers.html", context, status_code=status_code)


@router.get("/workers", response_class=HTMLResponse)
def workers_page(request: Request) -> HTMLResponse:
    return _workers_page(request)


@router.post("/workers/{worker_id}/rename")
def rename_worker(request: Request, worker_id: str, form: dict = Depends(read_form)) -> Response:
    """The name field of a worker row: the display name every page shows."""
    name = form_text(form, "name")
    if name is None:
        return _workers_page(request, text("workers.name_required"), 400)
    return apply_change(
        lambda: admin_client_for(request).rename_worker(worker_id, name),
        WORKERS_PATH,
        "worker_renamed",
        lambda error, status: _workers_page(request, error, status),
    )


@router.post("/workers/{worker_id}/make-master")
def make_master(request: Request, worker_id: str, form: dict = Depends(read_form)) -> Response:
    """ "Tornar master" (asked to confirm on the page: the current master is demoted)."""
    return apply_change(
        lambda: admin_client_for(request).make_master(worker_id),
        WORKERS_PATH,
        "master_changed",
        lambda error, status: _workers_page(request, error, status),
    )


@router.post("/workers/{worker_id}/revoke")
def revoke_worker(request: Request, worker_id: str, form: dict = Depends(read_form)) -> Response:
    return apply_change(
        lambda: admin_client_for(request).revoke_worker(worker_id),
        WORKERS_PATH,
        "worker_revoked",
        lambda error, status: _workers_page(request, error, status),
    )


# --- Configurações (settings) ---


def settings_page_response(
    request: Request,
    error: str | None = None,
    status_code: int = 200,
    pass_results_form: PassResultsForm | None = None,
):
    """The Settings page; `pass_results_form` keeps what was typed in the
    "Resultados das passagens" block (routes_admin_pass_results.py)."""
    admin = admin_client_for(request)
    page = admin.settings()
    context = admin_context(
        request,
        "settings",
        values=page.settings,
        status=page.status,
        intake_modes=INTAKE_MODE_LABELS,
        team_name_styles=TEAM_NAME_STYLE_LABELS,
        school_name_styles=SCHOOL_NAME_STYLE_LABELS,
        welcome_placeholders=PLACEHOLDERS,
        welcome_examples=EXAMPLE_VALUES,
        welcome_preview=fill_welcome_message(page.settings.welcome_message, EXAMPLE_VALUES),
        welcome_max_length=MAX_WELCOME_MESSAGE_LENGTH,
        vertical_message_max_length=MAX_VERTICAL_VIDEO_MESSAGE_LENGTH,
        duplicate_message_max_length=MAX_DUPLICATE_VIDEO_MESSAGE_LENGTH,
        audit=audit_lines(admin.audit(AdminArea.SETTINGS)),
        # "Resultados das passagens" (LIVESTACK-47), its own form on the page.
        config_max_length=MAX_PASS_RESULTS_CONFIG_LENGTH,
        pass_results=pass_results_block(
            saved_pass_results(admin), pass_results_form or PassResultsForm(), utc_now()
        ),
    )
    context["error"] = error
    return render_page(request, "admin/settings.html", context, status_code=status_code)


def saved_pass_results(admin) -> PassResultsSettings | None:
    """GET /admin/pass-results, or None when it cannot be read (the page still works)."""
    try:
        return admin.pass_results()
    except OrchestratorError as error:
        if error.status_code in (401, 403):
            raise
        return None
    except OrchestratorUnavailableError:
        return None


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request) -> HTMLResponse:
    return settings_page_response(request)


def _number(form: dict, name: str) -> float:
    """A number typed in the form; a decimal comma is fine. ValueError otherwise."""
    return float(form.get(name, "").replace(",", "."))


def _optional_number(form: dict, name: str) -> float | None:
    """Like _number, but a field left out or blank keeps its stored value (None)."""
    return _number(form, name) if form.get(name, "").strip() else None


def _optional_whole_number(form: dict, name: str) -> int | None:
    """A whole number typed in the form ("14"); blank or left out keeps the stored value."""
    value = _optional_number(form, name)
    if value is None:
        return None
    if not value.is_integer():
        raise ValueError(f"{name} must be a whole number")
    return int(value)


def _optional_checkbox(form: dict, name: str) -> bool | None:
    """A ticked box sends its name, an unticked one nothing. The page also sends
    "<name>_shown", so a form without the box (an older page) keeps the stored value."""
    if not form.get(f"{name}_shown"):
        return None
    return bool(form.get(name))


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
            # A form without the choice (an older page) keeps the stored one.
            clip_team_name_style=form.get("clip_team_name_style") or None,
            clip_school_name_style=form.get("clip_school_name_style") or None,
            originals_keep_hours=_optional_number(form, "originals_keep_hours"),
            originals_cap_gb=_optional_number(form, "originals_cap_gb"),
            # Kept as typed (empty = no welcome message); the orchestrator
            # trims it. A form without the field (an older page) keeps it.
            welcome_message=form.get("welcome_message"),
            # The bot's reply to a vertical video (LIVESTACK-50); same rules.
            vertical_video_message=form.get("vertical_video_message"),
            # The bot's reply to a duplicate video (LIVESTACK-51); same rules.
            duplicate_video_message=form.get("duplicate_video_message"),
            sender_messages_keep_days=_optional_whole_number(form, "sender_messages_keep_days"),
            # "Gerar prévia dos clipes" and "Baixar clipe" (LIVESTACK-52).
            clip_previews_enabled=_optional_checkbox(form, "clip_previews_enabled"),
            clip_download_keep_minutes=_optional_number(form, "clip_download_keep_minutes"),
            clip_download_unused_hours=_optional_number(form, "clip_download_unused_hours"),
        )
    except ValueError:
        return settings_page_response(
            request,
            text("settings.invalid"),
            400,
        )
    return apply_change(
        lambda: admin_client_for(request).update_settings(body),
        SETTINGS_PATH,
        "settings_updated",
        lambda error, status: settings_page_response(request, error, status),
    )
