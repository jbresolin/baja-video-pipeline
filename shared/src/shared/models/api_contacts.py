"""WhatsApp senders, as the admin Contacts page manages them (LIVESTACK-37).

Intake records every sender who writes to the bot as `unknown` on first
contact; the admin then allows or blocks them with one click.
"""

from datetime import datetime

from pydantic import Field

from shared.models.common import ApiModel
from shared.models.enums import ContactStatus


class Contact(ApiModel):
    # Digits only, from the webhook's remoteJid, e.g. "5511987654321".
    phone: str
    # Editable by the admin; prefilled from the WhatsApp profile name.
    display_name: str | None = None
    # The WhatsApp profile name of the newest message (pushName).
    push_name: str | None = None
    status: ContactStatus
    # Jobs from this sender start with this prova filled in.
    default_prova_id: int | None = None
    # That prova's name, for display (None without a default prova).
    default_prova: str | None = None
    notes: str | None = None
    # None for a contact the admin added before they ever wrote.
    first_seen_at: datetime | None = None
    last_seen_at: datetime | None = None


class ContactsResponse(ApiModel):
    # Newest contact (last message) first.
    contacts: list[Contact]


class CreateContactRequest(ApiModel):
    """Add a sender before they write (e.g. to allow them in allowlist mode)."""

    # Spaces, dashes, brackets and a leading + are dropped; 8 to 20 digits remain.
    phone: str = Field(min_length=8, max_length=32)
    display_name: str | None = Field(default=None, max_length=100)
    status: ContactStatus = ContactStatus.ALLOWED
    default_prova_id: int | None = None
    notes: str | None = Field(default=None, max_length=500)


class UpdateContactRequest(ApiModel):
    """PATCH /admin/contacts/{phone}.

    A field left out keeps its value; display_name, default_prova_id and
    notes may be sent as null to clear them.
    """

    display_name: str | None = Field(default=None, max_length=100)
    status: ContactStatus | None = None
    default_prova_id: int | None = None
    notes: str | None = Field(default=None, max_length=500)
