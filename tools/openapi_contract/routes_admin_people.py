"""Contract: admin Users and Contacts pages, and the provas list (admin page, LIVESTACK-37).

Every /admin/* route accepts a session of a user with role admin or the
static admin token (scripts); a reviewer's session gets 403. Every change
is written to the event log with the record before and after (GET
/admin/audit).
"""

from fastapi import APIRouter, Depends, Query

from shared.models import (
    Contact,
    ContactCreated,
    ContactsResponse,
    ContactStatus,
    CreateContactRequest,
    CreateUserRequest,
    UpdateContactRequest,
    UpdateUserRequest,
    User,
    UsersResponse,
    WelcomeSendResponse,
)
from tools.openapi_contract.auth import (
    admin_bearer,
    error_responses,
    not_implemented,
    session_bearer,
)

router = APIRouter()

# An admin session or the admin token (two dependencies = either one).
ADMIN_ONLY = [Depends(session_bearer), Depends(admin_bearer)]


@router.get(
    "/admin/users",
    tags=["admin"],
    summary="Review UI users (no password hashes)",
    response_model=UsersResponse,
    dependencies=ADMIN_ONLY,
    responses=error_responses(401, 403),
)
def list_users() -> UsersResponse:
    not_implemented()


@router.post(
    "/admin/users",
    tags=["admin"],
    summary="Add a user (reviewer or admin)",
    response_model=User,
    status_code=201,
    dependencies=ADMIN_ONLY,
    responses=error_responses(400, 401, 403, 409),
)
def create_user(body: CreateUserRequest) -> User:
    """409 `already_exists` for a taken username. The first admin on a new
    VPS without reviewers.txt is made with this route and the admin token."""
    not_implemented()


@router.patch(
    "/admin/users/{username}",
    tags=["admin"],
    summary="Change a user's role, disable/enable them, or set a new password",
    response_model=User,
    dependencies=ADMIN_ONLY,
    responses=error_responses(400, 401, 403, 404, 409),
)
def update_user(username: str, body: UpdateUserRequest) -> User:
    """Users are never deleted, only disabled. Disabling a user or setting
    their password ends their open sessions. Demoting or disabling the last
    enabled admin → 409 `conflict`."""
    not_implemented()


@router.get(
    "/admin/contacts",
    tags=["admin"],
    summary="WhatsApp senders, newest activity first",
    response_model=ContactsResponse,
    dependencies=ADMIN_ONLY,
    responses=error_responses(400, 401, 403),
)
def list_contacts(status: ContactStatus | None = Query(None)) -> ContactsResponse:
    """Every sender who wrote to the bot is recorded as `unknown` on first
    contact (DMs only; groups are ignored entirely)."""
    not_implemented()


@router.post(
    "/admin/contacts",
    tags=["admin"],
    summary="Add a sender before they write (e.g. to allow them in allowlist mode)",
    response_model=ContactCreated,
    status_code=201,
    dependencies=ADMIN_ONLY,
    responses=error_responses(400, 401, 403, 409),
)
def create_contact(body: CreateContactRequest) -> ContactCreated:
    """With `send_welcome: true` the Settings page's welcome message
    (`AdminSettings.welcome_message`, {nome} and {prova} filled in) is sent
    to the new contact through Evolution sendText after the contact is
    stored. The contact is created whatever happens to the message:
    `welcome` says whether Evolution accepted it, and why not (Evolution's
    answer; a blocked contact or an empty message is not tried). Each try is
    an event, `welcome_sent` or `welcome_failed`."""
    not_implemented()


@router.patch(
    "/admin/contacts/{phone}",
    tags=["admin"],
    summary="Allow/block a sender, rename them, set their default prova or notes",
    response_model=Contact,
    dependencies=ADMIN_ONLY,
    responses=error_responses(400, 401, 403, 404),
)
def update_contact(phone: str, body: UpdateContactRequest) -> Contact:
    """Intake by status and intake mode (Settings):

    | status | open | allowlist |
    | --- | --- | --- |
    | allowed | job | job |
    | unknown | job | no job; "não autorizado" reply at most once per 24 h |
    | blocked | no job, no reply | no job, no reply |

    Jobs from a sender with a default prova start with it filled in.
    """
    not_implemented()


@router.post(
    "/admin/contacts/{phone}/welcome",
    tags=["admin"],
    summary="Send the welcome message to this contact (again)",
    response_model=WelcomeSendResponse,
    dependencies=ADMIN_ONLY,
    responses=error_responses(401, 403, 404, 409),
)
def send_welcome(phone: str) -> WelcomeSendResponse:
    """The "Enviar boas-vindas" button on the Contacts page, e.g. after the message was
    edited. 409 `conflict` for a blocked contact or an empty welcome
    message. Evolution refusing the text is not an error: the answer is 200
    with `welcome.sent: false` and Evolution's reason, and the event
    `welcome_failed` is written, as on create. A send Evolution accepts sets
    `Contact.welcome_sent_at`."""
    not_implemented()
