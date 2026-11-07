"""Admin Contacts page: list senders, allow/block them, name them, set a default prova.

Adding a contact may also send them the welcome message (domain/welcome.py).
"""

import sqlite3
from typing import Any

from orchestrator.context import OrchestratorContext
from orchestrator.domain.audit import AdminActor, record_admin_change
from orchestrator.domain.contacts import normalize_phone
from orchestrator.domain.welcome import welcome_after_create
from orchestrator.errors import ApiError, not_found, validation_error
from orchestrator.repositories import catalog as catalog_repo
from orchestrator.repositories import contacts as contacts_repo
from shared.models import (
    Contact,
    ContactCreated,
    ContactsResponse,
    ContactStatus,
    CreateContactRequest,
    ErrorCode,
    UpdateContactRequest,
)


def _contact_json(contact: Contact) -> dict[str, Any]:
    return contact.model_dump(
        mode="json", include={"phone", "display_name", "status", "default_prova", "notes"}
    )


def _check_prova_exists(connection: sqlite3.Connection, prova_id: int | None) -> None:
    if prova_id is not None and catalog_repo.get_prova_row(connection, prova_id) is None:
        raise validation_error(f"there is no prova with id {prova_id}", prova_id=prova_id)


def _clean_text(value: str | None) -> str | None:
    return (value or "").strip() or None


def list_contacts(context: OrchestratorContext, status: ContactStatus | None) -> ContactsResponse:
    with context.database.transaction() as connection:
        rows = contacts_repo.list_contact_rows(connection, status)
    return ContactsResponse(
        contacts=[contacts_repo.contact_from_row(row) for row in rows],
        welcome_message_set=bool(context.admin_settings.current.welcome_message),
    )


def create_contact(
    context: OrchestratorContext, actor: AdminActor, request: CreateContactRequest
) -> ContactCreated:
    phone = normalize_phone(request.phone)
    if phone is None:
        raise validation_error("phone must have 8 to 20 digits, e.g. 5511987654321")
    now = context.clock.now()
    with context.database.transaction() as connection:
        if contacts_repo.get_contact_row(connection, phone) is not None:
            raise ApiError(409, ErrorCode.ALREADY_EXISTS, f"contact {phone} already exists")
        _check_prova_exists(connection, request.default_prova_id)
        contacts_repo.insert_contact(
            connection,
            phone,
            now,
            status=request.status,
            display_name=_clean_text(request.display_name),
            default_prova_id=request.default_prova_id,
            notes=_clean_text(request.notes),
        )
        contact = contacts_repo.contact_from_row(contacts_repo.get_contact_row(connection, phone))
        record_admin_change(
            connection,
            now,
            actor,
            "contact_created",
            f"{phone} added as {request.status}",
            after=_contact_json(contact),
        )
    # After the commit: the contact exists whatever happens to the message.
    welcome = welcome_after_create(context, actor, contact) if request.send_welcome else None
    if welcome is not None and welcome.sent:
        # Read again for its welcome_sent_at.
        with context.database.transaction() as connection:
            row = contacts_repo.get_contact_row(connection, phone)
        contact = contacts_repo.contact_from_row(row)
    return ContactCreated(**contact.model_dump(), welcome=welcome)


def update_contact(
    context: OrchestratorContext, actor: AdminActor, phone: str, request: UpdateContactRequest
) -> Contact:
    # Only fields present in the request change; null clears a clearable one.
    sent = request.model_fields_set
    now = context.clock.now()
    with context.database.transaction() as connection:
        row = contacts_repo.get_contact_row(connection, phone)
        if row is None:
            raise not_found("contact")
        before = contacts_repo.contact_from_row(row)
        changes: dict[str, Any] = {}
        if "status" in sent:
            if request.status is None:
                raise validation_error("status cannot be null")
            changes["status"] = str(request.status)
        if "default_prova_id" in sent:
            _check_prova_exists(connection, request.default_prova_id)
            changes["default_prova_id"] = request.default_prova_id
        for name in ("display_name", "notes"):
            if name in sent:
                changes[name] = _clean_text(getattr(request, name))
        if changes:
            contacts_repo.update_contact(connection, phone, now, changes)
        after = contacts_repo.contact_from_row(contacts_repo.get_contact_row(connection, phone))
        changed = [name for name in changes if getattr(before, name) != getattr(after, name)]
        record_admin_change(
            connection,
            now,
            actor,
            "contact_updated",
            f"{phone}: " + (", ".join(changed) or "no change"),
            before=_contact_json(before),
            after=_contact_json(after),
        )
    return after
