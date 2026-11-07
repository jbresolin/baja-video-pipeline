"""Reading a non-video WhatsApp message out of a webhook (domain/whatsapp_notes.py).

The intake tests post whole webhooks in Evolution 2.3.7's shapes. These
check, on bare dicts, the other shapes the reader also accepts, so a
different Evolution build or a WhatsApp wrapper does not lose a message.
"""

from orchestrator.domain.whatsapp_notes import (
    MAX_TEXT_LENGTH,
    MessageChange,
    deleted_message_id,
    read_change,
    read_note,
)
from shared.models import SenderMessageKind


def test_a_reply_in_whatsapps_own_shape_keeps_its_quote():
    # Without Evolution's rewrite: an extendedTextMessage with its contextInfo inside.
    message = {
        "extendedTextMessage": {"text": "errei, é a 3", "contextInfo": {"stanzaId": "3EB0VIDEO"}}
    }
    note = read_note(message)
    assert (note.kind, note.text, note.quoted_message_id) == (
        SenderMessageKind.TEXT,
        "errei, é a 3",
        "3EB0VIDEO",
    )


def test_disappearing_and_view_once_wrappers_are_opened():
    # "Mensagens temporárias" wrap the message in ephemeralMessage; a "view
    # once" photo in viewOnceMessageV2. The content is one level down.
    ephemeral = {"ephemeralMessage": {"message": {"conversation": "carro 42"}}}
    view_once = {"viewOnceMessageV2": {"message": {"imageMessage": {"caption": "o 42"}}}}
    assert read_note(ephemeral).text == "carro 42"
    assert (read_note(view_once).kind, read_note(view_once).text) == (
        SenderMessageKind.IMAGE,
        "o 42",
    )


def test_a_document_with_a_caption_in_whatsapps_wrapper():
    message = {
        "documentWithCaptionMessage": {
            "message": {"documentMessage": {"mimetype": "application/pdf", "caption": "ficha"}}
        }
    }
    note = read_note(message)
    assert (note.kind, note.text) == (SenderMessageKind.DOCUMENT, "ficha")


def test_kinds_without_text_and_unknown_kinds():
    assert read_note({"locationMessage": {"degreesLatitude": -23.5}}).kind == "location"
    assert read_note({"contactMessage": {"displayName": "Ana"}}).text is None
    # Something WhatsApp added that we do not know: kept as "(mensagem)".
    assert read_note({"pollCreationMessageV3": {"name": "?"}}).kind == "other"


def test_nothing_to_show_is_none():
    # Only bookkeeping, an empty text, a reaction: not kept at all.
    assert read_note({"messageContextInfo": {}}) is None
    assert read_note({"conversation": "   "}) is None
    assert read_note({"reactionMessage": {"text": "👍"}}) is None
    assert read_note(None) is None


def test_a_very_long_text_is_cut():
    note = read_note({"conversation": "a" * (MAX_TEXT_LENGTH + 50)})
    assert len(note.text) == MAX_TEXT_LENGTH and note.text.endswith("…")


def test_changes_from_a_protocol_message():
    key = {"remoteJid": "5511@s.whatsapp.net", "fromMe": True, "id": "3EB0TEXT"}
    assert read_change({"key": key, "type": 0}) == MessageChange("3EB0TEXT", deleted=True)
    # An edited caption of an image counts as the new text.
    edit = {
        "key": key,
        "type": "MESSAGE_EDIT",
        "editedMessage": {"imageMessage": {"caption": "42"}},
    }
    assert read_change(edit) == MessageChange("3EB0TEXT", deleted=False, new_text="42")
    # Other protocol messages (history sync, ephemeral setting...) are not changes.
    assert read_change({"key": key, "type": "EPHEMERAL_SETTING"}) is None
    assert read_change({"type": 0}) is None


def test_the_id_in_a_delete_event_flat_or_under_key():
    assert deleted_message_id({"id": "A", "remoteJid": "x", "status": "DELETED"}) == "A"
    assert deleted_message_id({"key": {"id": "B"}}) == "B"
    assert deleted_message_id({"status": "DELETED"}) is None
