"""Admin pages: index, Usuários (users) and Contatos (WhatsApp senders).

Contatos also sends the welcome message (LIVESTACK-44): a checkbox on the
add form and "Enviar boas-vindas" on each row. A send the orchestrator
could not make is shown on the page with its reason, as a warning: the
contact itself was saved.
"""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from markupsafe import Markup

from review_ui.admin_view import (
    CONTACT_STATUS_LABELS,
    ROLE_LABELS,
    admin_context,
    apply_change,
    audit_lines,
    form_int,
    form_text,
    redirect_done,
    show_refusal,
)
from review_ui.catalog import markup, text
from review_ui.forms import read_form
from review_ui.orchestrator_client import OrchestratorError
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


def _contacts_page(
    request: Request,
    error: str | None = None,
    status_code: int = 200,
    *,
    warning: Markup | None = None,
):
    admin = admin_client_for(request)
    status_filter = request.query_params.get("status") or None
    chosen = ContactStatus(status_filter) if status_filter in set(ContactStatus) else None
    listing = admin.contacts(chosen)
    context = admin_context(
        request,
        "contacts",
        contacts=listing.contacts,
        welcome_message_set=listing.welcome_message_set,
        chosen_status=chosen,
        provas=admin.provas(),
        status_labels=CONTACT_STATUS_LABELS,
        audit=audit_lines(admin.audit(AdminArea.CONTACTS)),
    )
    context["error"] = error
    context["warning"] = warning
    return render_page(request, "admin/contacts.html", context, status_code=status_code)


def _welcome_not_sent(key: str, reason: str | None) -> Markup:
    """ "…, mas a mensagem não foi enviada: <reason>", the reason as it came (English)."""
    return markup(key, reason=Markup('<span lang="en">{}</span>').format(reason or ""))


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
            # The checkbox "Enviar mensagem de boas-vindas" (LIVESTACK-44).
            send_welcome=form.get("send_welcome") == "1",
        )
    except ValueError:
        return _contacts_page(request, text("contacts.invalid_phone"), 400)
    try:
        created = admin_client_for(request).create_contact(body)
    except OrchestratorError as error:
        return show_refusal(error, lambda reason, status: _contacts_page(request, reason, status))
    if created.welcome is None:
        return redirect_done(CONTACTS_PATH, "contact_created")
    if created.welcome.sent:
        return redirect_done(CONTACTS_PATH, "contact_created_welcome_sent")
    # The contact exists; only the message failed. Shown at once, since the
    # reason cannot travel in the redirect's URL (?feito= keys are fixed).
    return _contacts_page(
        request,
        warning=_welcome_not_sent("contacts.welcome.create_failed", created.welcome.error),
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
        # "Aplicar … aos vídeos …?" answered yes (js/admin.js, LIVESTACK-51).
        changes["apply_default_prova_to_waiting"] = (
            form.get("apply_default_prova_to_waiting") == "1"
        )
    try:
        body = UpdateContactRequest(**changes)
    except ValueError:
        return _contacts_page(request, text("error.validation_error"), 400)
    applied = changes.get("apply_default_prova_to_waiting", False)
    return apply_change(
        lambda: admin_client_for(request).update_contact(phone, body),
        CONTACTS_PATH,
        "contact_updated_prova_applied" if applied else "contact_updated",
        lambda error, status: _contacts_page(request, error, status),
    )


@router.post("/contacts/{phone}/welcome")
def send_welcome(request: Request, phone: str, form: dict = Depends(read_form)) -> Response:
    """ "Enviar boas-vindas" on a contact's row (the form asks to confirm first)."""
    try:
        result = admin_client_for(request).send_welcome(phone)
    except OrchestratorError as error:
        # 409: blocked contact or empty message (the button is disabled then).
        return show_refusal(error, lambda reason, status: _contacts_page(request, reason, status))
    if result.welcome.sent:
        return redirect_done(CONTACTS_PATH, "welcome_sent")
    return _contacts_page(
        request,
        warning=_welcome_not_sent("contacts.welcome.send_failed", result.welcome.error),
    )
