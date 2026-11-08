"""WhatsApp senders: recorded on first contact, then allowed or blocked by an admin.

Intake asks two questions here for every private message:

1. record_sender(): remember the sender (status `unknown` the first time),
   with their WhatsApp profile name and when they last wrote.
2. intake_decision(): may this sender's video become a job?

    status \\ intake mode   open                allowlist
    allowed                job                 job
    unknown                job                 no job, refusal reply*
    blocked                no job, no reply    no job, no reply

    * SENDER_NOT_AUTHORIZED, at most once per 24 h per sender.

An allowed or unknown sender's default prova (if any) is filled into
their new jobs; the reviewer can still change it.

Contacts are keyed by one form of the phone number (domain/phones.py), so
a number the admin typed with the mobile 9th digit and a WhatsApp JID
without it are the same contact (LIVESTACK-51).
"""

import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta

from orchestrator.db.database import from_db_time, to_db_time
from orchestrator.domain.phones import canonical_phone_from_jid_digits, canonical_typed_phone
from orchestrator.repositories import catalog as catalog_repo
from orchestrator.repositories import contacts as contacts_repo
from shared.models import ContactStatus, IntakeMode

REFUSAL_REPLY_INTERVAL = timedelta(hours=24)


def phone_from_jid(remote_jid: str) -> str:
    """The sender's number as the JID writes it, digits only.

    "5511987654321@s.whatsapp.net" → "5511987654321". A device suffix
    ("5511987654321:12@s.whatsapp.net") is dropped too. Not the contact
    key: see contact_phone_from_jid.
    """
    user_part = remote_jid.split("@", 1)[0].split(":", 1)[0]
    return re.sub(r"\D", "", user_part)


def contact_phone_from_jid(remote_jid: str) -> str:
    """The contact key of a JID: "551981234567@s.whatsapp.net" → "5519981234567"."""
    return canonical_phone_from_jid_digits(phone_from_jid(remote_jid))


def sender_phone(sender_jid: str | None) -> str | None:
    """The contact key of a job's or message's sender JID; None without one (test uploads)."""
    if not sender_jid:
        return None
    return contact_phone_from_jid(sender_jid) or None


def normalize_phone(typed: str) -> str | None:
    """The contact key of a number an admin typed ("+55 (11) 98765-4321",
    "19 98123-4567"), or None if implausible (domain/phones.py)."""
    return canonical_typed_phone(typed)


def record_sender(
    connection: sqlite3.Connection, remote_jid: str, push_name: str | None, now: datetime
) -> sqlite3.Row:
    """Insert the sender as `unknown`, or update their last-seen time and profile name.

    The display name is prefilled from the profile name only while it is
    empty, so a name the admin typed is never overwritten.
    """
    phone = contact_phone_from_jid(remote_jid)
    row = contacts_repo.get_contact_row(connection, phone)
    if row is None:
        contacts_repo.insert_contact(
            connection,
            phone,
            now,
            status=ContactStatus.UNKNOWN,
            display_name=push_name,
            push_name=push_name,
            seen=True,
        )
    else:
        changes = {"last_seen_at": to_db_time(now)}
        if row["first_seen_at"] is None:
            changes["first_seen_at"] = to_db_time(now)
        if push_name:
            changes["push_name"] = push_name
            if not row["display_name"]:
                changes["display_name"] = push_name
        contacts_repo.update_contact(connection, phone, now, changes)
    return contacts_repo.get_contact_row(connection, phone)


@dataclass(frozen=True)
class IntakeDecision:
    accept: bool
    # Why a video was not accepted (for the webhook's `reason`), else None.
    reason: str | None = None
    # Send bot_messages.SENDER_NOT_AUTHORIZED now (allowlist refusal, ≤ 1 per 24 h).
    send_refusal: bool = False
    # Name of the sender's default prova, filled into the new job.
    default_prova: str | None = None


def messages_are_kept(contact_row: sqlite3.Row, mode: IntakeMode) -> bool:
    """Are this sender's other messages kept for the job page? (LIVESTACK-49)

    Exactly when their videos would become jobs (the table above), but
    never with a reply: texts are only read, not answered.
    """
    status = ContactStatus(contact_row["status"])
    if status == ContactStatus.BLOCKED:
        return False
    return mode == IntakeMode.OPEN or status == ContactStatus.ALLOWED


def intake_decision(
    connection: sqlite3.Connection, contact_row: sqlite3.Row, mode: IntakeMode, now: datetime
) -> IntakeDecision:
    """May this sender's video become a job? (table in the module docstring)

    Records the refusal reply's time when it decides to send one, so the
    next refusal within 24 h stays silent.
    """
    status = ContactStatus(contact_row["status"])
    if status == ContactStatus.BLOCKED:
        return IntakeDecision(accept=False, reason="sender is blocked")
    if mode == IntakeMode.ALLOWLIST and status != ContactStatus.ALLOWED:
        last_reply = from_db_time(contact_row["last_refusal_reply_at"])
        send_refusal = last_reply is None or now - last_reply >= REFUSAL_REPLY_INTERVAL
        if send_refusal:
            contacts_repo.update_contact(
                connection, contact_row["phone"], now, {"last_refusal_reply_at": to_db_time(now)}
            )
        return IntakeDecision(
            accept=False, reason="sender is not on the allowlist", send_refusal=send_refusal
        )
    default_prova = None
    if contact_row["default_prova_id"] is not None:
        prova_row = catalog_repo.get_prova_row(connection, contact_row["default_prova_id"])
        default_prova = prova_row["name"] if prova_row is not None else None
    return IntakeDecision(accept=True, default_prova=default_prova)
