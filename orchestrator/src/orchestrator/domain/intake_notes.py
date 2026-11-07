"""Intake of the sender's messages that are not videos (LIVESTACK-49).

domain/intake.py hands over:

* a messages.upsert that carries no video, after recording the sender
  (keep_incoming_note): kept when the sender's videos would be accepted
  (contacts.messages_are_kept), never answered;
* the messages.edited and messages.delete events (handle_change_event):
  applied to a kept message, ignored otherwise.

Every answer is 200, so Evolution never retries these.
"""

import sqlite3
from datetime import datetime
from typing import Any

from orchestrator.context import OrchestratorContext
from orchestrator.domain import contacts, sender_messages, whatsapp_notes
from orchestrator.domain.recording_time import sent_at_from_timestamp
from shared.models import EvolutionWebhook, IntakeResponse

EDITED_EVENT = "messages.edited"
DELETE_EVENT = "messages.delete"
CHANGE_EVENTS = (EDITED_EVENT, DELETE_EVENT)


def _ignored(reason: str, message_id: str | None, stored: bool = False) -> IntakeResponse:
    # `ignored`: no job came of it. `message_stored`: the job page will show it.
    return IntakeResponse(
        job_id=None, message_id=message_id, ignored=True, reason=reason, message_stored=stored
    )


def keep_incoming_note(
    context: OrchestratorContext,
    connection: sqlite3.Connection,
    webhook: EvolutionWebhook,
    payload: dict[str, Any],
    contact_row: sqlite3.Row,
    now: datetime,
) -> IntakeResponse:
    """A private message without a video, from a sender already recorded."""
    key = webhook.data.key
    raw_data = payload.get("data") or {}
    if not contacts.messages_are_kept(contact_row, context.admin_settings.current.intake_mode):
        return _ignored("not a video; messages of this sender are not kept", key.id)

    # Another Evolution version may upsert an edit or deletion instead of
    # sending messages.edited; here the sender is known, which is better.
    protocol = whatsapp_notes.protocol_message_in(raw_data.get("message"))
    if protocol is not None:
        return _apply(connection, whatsapp_notes.read_change(protocol), now, key.id)

    note = whatsapp_notes.read_note(raw_data.get("message"), raw_data.get("contextInfo"))
    if note is None:
        return _ignored("not a video and nothing to show (a reaction, a vote, ...)", key.id)
    stored = sender_messages.keep_message(
        connection,
        message_id=key.id,
        sender_jid=key.sender_jid,
        note=note,
        received_at=now,
        sent_at=sent_at_from_timestamp(webhook.data.message_timestamp),
    )
    if not stored:
        return IntakeResponse(
            job_id=None, message_id=key.id, duplicate=True, reason="duplicate message"
        )
    return _ignored("not a video; kept for the job page", key.id, stored=True)


def handle_change_event(context: OrchestratorContext, payload: dict[str, Any]) -> IntakeResponse:
    """messages.edited (an edit or a deletion) or messages.delete (a deletion).

    Shapes in domain/whatsapp_notes.py. Nothing is validated beyond what
    is read: an event we cannot read is acknowledged and ignored.
    """
    data = payload.get("data")
    if payload.get("event") == EDITED_EVENT:
        change = whatsapp_notes.read_change(data)
    else:
        message_id = whatsapp_notes.deleted_message_id(data)
        change = (
            whatsapp_notes.MessageChange(message_id=message_id, deleted=True)
            if message_id
            else None
        )
    with context.database.transaction() as connection:
        return _apply(connection, change, context.clock.now(), None)


def _apply(
    connection: sqlite3.Connection,
    change: whatsapp_notes.MessageChange | None,
    now: datetime,
    message_id: str | None,
) -> IntakeResponse:
    if change is None:
        return _ignored("not an edit or deletion we can read", message_id)
    if not sender_messages.apply_change(connection, change, now):
        return _ignored("edit or deletion of a message that is not kept", change.message_id)
    what = "deletion" if change.deleted else "edit"
    return _ignored(f"{what} applied to a kept message", change.message_id, stored=True)
