"""Routes: admin Users and Contacts pages (admin session or the admin token)."""

from fastapi import APIRouter, Depends, Query

from orchestrator.api.auth import get_context, require_admin
from orchestrator.context import OrchestratorContext
from orchestrator.domain import contacts_admin, users_admin
from orchestrator.domain.audit import AdminActor
from shared.models import (
    Contact,
    ContactsResponse,
    ContactStatus,
    CreateContactRequest,
    CreateUserRequest,
    UpdateContactRequest,
    UpdateUserRequest,
    User,
    UsersResponse,
)

router = APIRouter()


@router.get(
    "/admin/users",
    tags=["admin"],
    response_model=UsersResponse,
    dependencies=[Depends(require_admin)],
)
def list_users(context: OrchestratorContext = Depends(get_context)) -> UsersResponse:
    return users_admin.list_users(context)


@router.post("/admin/users", tags=["admin"], response_model=User, status_code=201)
def create_user(
    body: CreateUserRequest,
    actor: AdminActor = Depends(require_admin),
    context: OrchestratorContext = Depends(get_context),
) -> User:
    return users_admin.create_user(context, actor, body)


@router.patch("/admin/users/{username}", tags=["admin"], response_model=User)
def update_user(
    username: str,
    body: UpdateUserRequest,
    actor: AdminActor = Depends(require_admin),
    context: OrchestratorContext = Depends(get_context),
) -> User:
    return users_admin.update_user(context, actor, username, body)


@router.get(
    "/admin/contacts",
    tags=["admin"],
    response_model=ContactsResponse,
    dependencies=[Depends(require_admin)],
)
def list_contacts(
    status: ContactStatus | None = Query(None),
    context: OrchestratorContext = Depends(get_context),
) -> ContactsResponse:
    return contacts_admin.list_contacts(context, status)


@router.post("/admin/contacts", tags=["admin"], response_model=Contact, status_code=201)
def create_contact(
    body: CreateContactRequest,
    actor: AdminActor = Depends(require_admin),
    context: OrchestratorContext = Depends(get_context),
) -> Contact:
    return contacts_admin.create_contact(context, actor, body)


@router.patch("/admin/contacts/{phone}", tags=["admin"], response_model=Contact)
def update_contact(
    phone: str,
    body: UpdateContactRequest,
    actor: AdminActor = Depends(require_admin),
    context: OrchestratorContext = Depends(get_context),
) -> Contact:
    return contacts_admin.update_contact(context, actor, phone, body)
