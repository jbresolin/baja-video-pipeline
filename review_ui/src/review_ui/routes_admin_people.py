"""Admin pages: index, Usuários (users) and Contatos (WhatsApp senders)."""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from review_ui.admin_view import (
    CONTACT_STATUS_LABELS,
    ROLE_LABELS,
    admin_context,
    apply_change,
    audit_lines,
    form_int,
    form_text,
)
from review_ui.catalog import text
from review_ui.forms import read_form
from review_ui.request_context import admin_client_for, render_page
from shared.models import (
    AdminArea,
    ContactStatus,
    CreateContactRequest,
    CreateUserRequest,
    UpdateContactRequest,
    UpdateUserRequest,
)

router = APIRouter(prefix="/review/admin")

USERS_PATH = "/review/admin/users"
CONTACTS_PATH = "/review/admin/contacts"


@router.get("", include_in_schema=False)
@router.get("/", include_in_schema=False)
def admin_index() -> RedirectResponse:
    return RedirectResponse(USERS_PATH, status_code=303)


# --- Usuários ---


def _users_page(request: Request, error: str | None = None, status_code: int = 200):
    admin = admin_client_for(request)
    context = admin_context(
        request,
        "users",
        users=admin.users(),
        role_labels=ROLE_LABELS,
        audit=audit_lines(admin.audit(AdminArea.USERS)),
    )
    context["error"] = error
    return render_page(request, "admin/users.html", context, status_code=status_code)


@router.get("/users", response_class=HTMLResponse)
def users_page(request: Request) -> HTMLResponse:
    return _users_page(request)


@router.post("/users/create")
def create_user(request: Request, form: dict = Depends(read_form)) -> Response:
    try:
        body = CreateUserRequest(
            username=form.get("username", "").strip(),
            password=form.get("password", ""),
            role=form.get("role", "reviewer"),
        )
    except ValueError:
        return _users_page(
            request,
            text("users.invalid"),
            400,
        )
    return apply_change(
        lambda: admin_client_for(request).create_user(body),
        USERS_PATH,
        "user_created",
        lambda error, status: _users_page(request, error, status),
    )


@router.post("/users/{username}")
def update_user(request: Request, username: str, form: dict = Depends(read_form)) -> Response:
    """One form per user row: role, enabled/disabled, and optionally a new password."""
    changes = {"role": form.get("role"), "disabled": form.get("disabled") == "1"}
    if form_text(form, "password"):
        changes["password"] = form["password"]
    try:
        body = UpdateUserRequest(**changes)
    except ValueError:
        return _users_page(request, text("users.password_length"), 400)
    return apply_change(
        lambda: admin_client_for(request).update_user(username, body),
        USERS_PATH,
        "user_updated",
        lambda error, status: _users_page(request, error, status),
    )


# --- Contatos ---


def _contacts_page(request: Request, error: str | None = None, status_code: int = 200):
    admin = admin_client_for(request)
    status_filter = request.query_params.get("status") or None
    chosen = ContactStatus(status_filter) if status_filter in set(ContactStatus) else None
    context = admin_context(
        request,
        "contacts",
        contacts=admin.contacts(chosen),
        chosen_status=chosen,
        provas=admin.provas(),
        status_labels=CONTACT_STATUS_LABELS,
        audit=audit_lines(admin.audit(AdminArea.CONTACTS)),
    )
    context["error"] = error
    return render_page(request, "admin/contacts.html", context, status_code=status_code)


@router.get("/contacts", response_class=HTMLResponse)
def contacts_page(request: Request) -> HTMLResponse:
    return _contacts_page(request)


@router.post("/contacts/create")
def create_contact(request: Request, form: dict = Depends(read_form)) -> Response:
    try:
        body = CreateContactRequest(
            phone=form.get("phone", ""),
            display_name=form_text(form, "display_name"),
            status=form.get("status", "allowed"),
            default_prova_id=form_int(form, "default_prova_id"),
            notes=form_text(form, "notes"),
        )
    except ValueError:
        return _contacts_page(request, text("contacts.invalid_phone"), 400)
    return apply_change(
        lambda: admin_client_for(request).create_contact(body),
        CONTACTS_PATH,
        "contact_created",
        lambda error, status: _contacts_page(request, error, status),
    )


@router.post("/contacts/{phone}")
def update_contact(request: Request, phone: str, form: dict = Depends(read_form)) -> Response:
    """One form per contact row. The quick Permitir/Bloquear buttons send only `status`."""
    changes = {}
    if "status" in form:
        changes["status"] = form["status"]
    if "display_name" in form:  # the full row form sends every field
        changes["display_name"] = form_text(form, "display_name")
        changes["default_prova_id"] = form_int(form, "default_prova_id")
        changes["notes"] = form_text(form, "notes")
    try:
        body = UpdateContactRequest(**changes)
    except ValueError:
        return _contacts_page(request, text("error.validation_error"), 400)
    return apply_change(
        lambda: admin_client_for(request).update_contact(phone, body),
        CONTACTS_PATH,
        "contact_updated",
        lambda error, status: _contacts_page(request, error, status),
    )
