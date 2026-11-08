"""The welcome message to a contact the admin adds (LIVESTACK-44).

When João adds a filmmaker on Admin → Contatos, the bot tells them which
number it is and how to send videos, so he no longer has to. The text is
the Settings page's `welcome_message` with {nome} and {prova} filled in
for the contact (shared.welcome_message).

It is sent in two ways:

* on create, when the request has `send_welcome: true` (the checkbox);
* on "Enviar boas-vindas" for one contact (POST /admin/contacts/{phone}/welcome).

It is never sent to a blocked contact, nor when the message is empty. A
send Evolution refuses (or cannot be reached for) never undoes anything:
the contact stays, the event `welcome_failed` keeps Evolution's reason,
and the answer carries it so the page can say so. A send Evolution
accepts is the event `welcome_sent` and sets `contacts.welcome_sent_at`.

The text goes to the contact's phone number with sendText, the same
client the bot's replies use, but not as a quoted reply: there is no
message of theirs to answer.
"""

from orchestrator.context import OrchestratorContext
from orchestrator.db.database import to_db_time
from orchestrator.domain.audit import AdminActor, record_admin_change
from orchestrator.domain.contacts import normalize_phone
from orchestrator.errors import ApiError, not_found
from orchestrator.repositories import contacts as contacts_repo
from shared.models import (
    Contact,
    ContactStatus,
    ErrorCode,
    WelcomeResult,
    WelcomeSendResponse,
)
from shared.welcome_message import fill_welcome_message

CONTACT_IS_BLOCKED = "the contact is blocked: no welcome message is sent to blocked contacts"
MESSAGE_IS_EMPTY = "the welcome message is empty (Settings)"


def welcome_text_for(template: str, contact: Contact) -> str:
    """The message for this contact: {nome} = display name, {prova} = default prova."""
    return fill_welcome_message(
        template, {"nome": contact.display_name, "prova": contact.default_prova}
    )


def why_welcome_cannot_be_sent(context: OrchestratorContext, contact: Contact) -> str | None:
    """Why nothing would be sent to this contact now, or None when it can be."""
    if contact.status == ContactStatus.BLOCKED:
        return CONTACT_IS_BLOCKED
    if not context.admin_settings.current.welcome_message:
        return MESSAGE_IS_EMPTY
    return None


def welcome_after_create(
    context: OrchestratorContext, actor: AdminActor, contact: Contact
) -> WelcomeResult:
    """The checkbox on the add form: send unless it cannot be, never failing the create.

    A blocked contact or an empty message is not an attempt: nothing is
    sent and no event is written; the result only says why.
    """
    reason = why_welcome_cannot_be_sent(context, contact)
    if reason is not None:
        return WelcomeResult(sent=False, error=reason)
    return _send_and_record(context, actor, contact)


def send_welcome_again(
    context: OrchestratorContext, actor: AdminActor, phone: str
) -> WelcomeSendResponse:
    """ "Enviar boas-vindas" on a contact's row (again, e.g. after editing the message).

    404 for an unknown contact; 409 `conflict` when it cannot be sent
    (blocked contact, empty message). Evolution refusing is not an error:
    the answer says `sent: false` with the reason, as on create.
    """
    contact = _contact(context, phone)
    reason = why_welcome_cannot_be_sent(context, contact)
    if reason is not None:
        raise ApiError(409, ErrorCode.CONFLICT, reason)
    result = _send_and_record(context, actor, contact)
    return WelcomeSendResponse(contact=_contact(context, phone), welcome=result)


def _contact(context: OrchestratorContext, phone: str) -> Contact:
    # The path's phone may be written any way (domain/phones.py).
    phone = normalize_phone(phone) or phone
    with context.database.transaction() as connection:
        row = contacts_repo.get_contact_row(connection, phone)
    if row is None:
        raise not_found("contact")
    return contacts_repo.contact_from_row(row)


def _send_and_record(
    context: OrchestratorContext, actor: AdminActor, contact: Contact
) -> WelcomeResult:
    """Send now (outside any DB transaction: Evolution may be slow), then record it."""
    text = welcome_text_for(context.admin_settings.current.welcome_message, contact)
    error = context.evolution.send_text_reporting_failure(contact.phone, text)
    now = context.clock.now()
    with context.database.transaction() as connection:
        if error is None:
            contacts_repo.update_contact(
                connection, contact.phone, now, {"welcome_sent_at": to_db_time(now)}
            )
            record_admin_change(
                connection,
                now,
                actor,
                "welcome_sent",
                f"welcome message sent to {contact.phone}",
                after={"phone": contact.phone, "text": text},
            )
        else:
            record_admin_change(
                connection,
                now,
                actor,
                "welcome_failed",
                f"welcome message to {contact.phone} not sent: {error}",
                after={"phone": contact.phone, "text": text, "error": error},
            )
    return WelcomeResult(sent=error is None, error=error)
