"""Read a filmmaker's message that is not a video out of an Evolution webhook (LIVESTACK-49).

Pure functions over the webhook's JSON (plain dicts): no database here.
domain/sender_messages.py stores what they return.

The shapes are Evolution 2.3.7's, read from its source
(src/api/integrations/channel/whatsapp/whatsapp.baileys.service.ts):

* `messages.upsert` goes through `prepareMessage`, which rewrites an
  `extendedTextMessage` (a text with a link or a reply) into a plain
  `conversation` with its text, and moves the message's `contextInfo` up
  to `data.contextInfo`. So the id of a quoted message is
  `data.contextInfo.stanzaId`. A `documentWithCaptionMessage` arrives
  flattened into `documentMessage`. Both original shapes are read too.
* Edits and deletions never come as `messages.upsert`. Any message with a
  `protocolMessage` is sent as event `messages.edited` with `data` = that
  protocolMessage, `{key, type, editedMessage, timestampMs}`: an edit
  (type MESSAGE_EDIT, 14) and a deletion for everyone (type REVOKE, 0)
  alike. `key.id` is the id of the message changed. Baileys may write the
  type as its number or its name; both are read.
* A deletion also comes as `messages.delete` with `data` = the deleted
  message's key plus `"status": "DELETED"`, but only when Evolution finds
  the message in its own store, so `messages.edited` is the one relied on.
"""

from dataclasses import dataclass
from typing import Any

from shared.models import SenderMessageKind

# A message's content is under one key named after its kind.
KIND_OF_CONTENT_KEY = {
    "conversation": SenderMessageKind.TEXT,
    "extendedTextMessage": SenderMessageKind.TEXT,
    "imageMessage": SenderMessageKind.IMAGE,
    "audioMessage": SenderMessageKind.AUDIO,
    "videoMessage": SenderMessageKind.VIDEO,
    "ptvMessage": SenderMessageKind.VIDEO,
    "documentMessage": SenderMessageKind.DOCUMENT,
    "stickerMessage": SenderMessageKind.STICKER,
    "locationMessage": SenderMessageKind.LOCATION,
    "liveLocationMessage": SenderMessageKind.LOCATION,
    "contactMessage": SenderMessageKind.CONTACT,
    "contactsArrayMessage": SenderMessageKind.CONTACT,
}

# Kinds whose text (or caption) is kept; for the others only the kind.
KINDS_WITH_TEXT = {
    SenderMessageKind.TEXT,
    SenderMessageKind.IMAGE,
    SenderMessageKind.VIDEO,
    SenderMessageKind.DOCUMENT,
}

# Keys that are not something the person said: WhatsApp's bookkeeping, a
# reaction (left as it is, by the brief), a poll vote, an edit or deletion
# (read_change), a pin. A message with nothing else is not kept.
NOT_A_MESSAGE = {
    "messageContextInfo",
    "senderKeyDistributionMessage",
    "reactionMessage",
    "encReactionMessage",
    "pollUpdateMessage",
    "protocolMessage",
    "editedMessage",
    "keepInChatMessage",
    "pinInChatMessage",
}

# Wrappers that carry the real message one level down, under "message".
WRAPPER_KEYS = (
    "ephemeralMessage",
    "viewOnceMessage",
    "viewOnceMessageV2",
    "viewOnceMessageV2Extension",
    "documentWithCaptionMessage",
)

# Baileys' ProtocolMessage.Type values that matter here.
REVOKE_TYPES = (0, "0", "REVOKE")
EDIT_TYPES = (14, "14", "MESSAGE_EDIT")

# WhatsApp allows ~65 000 characters; nobody needs that on the job page.
MAX_TEXT_LENGTH = 4000


@dataclass(frozen=True)
class IncomingNote:
    """A message that is not a video, as the job page will list it."""

    kind: SenderMessageKind
    # The text or caption, None when there is none (audio, sticker, ...).
    text: str | None
    # The message it quotes (a reply), else None.
    quoted_message_id: str | None


@dataclass(frozen=True)
class MessageChange:
    """The sender edited or deleted one of their messages."""

    message_id: str
    deleted: bool
    # The new text of an edit; None for a deletion.
    new_text: str | None = None


def _unwrap(message: dict[str, Any]) -> dict[str, Any]:
    """The message inside "disappearing" / "view once" / caption wrappers."""
    found_wrapper = True
    while found_wrapper:
        found_wrapper = False
        for wrapper in WRAPPER_KEYS:
            inner = message.get(wrapper)
            if isinstance(inner, dict) and isinstance(inner.get("message"), dict):
                message = inner["message"]
                found_wrapper = True
                break
    return message


def clean_text(value: Any) -> str | None:
    """Trimmed text, cut to MAX_TEXT_LENGTH; None when empty or not text."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value:
        return None
    if len(value) > MAX_TEXT_LENGTH:
        return value[: MAX_TEXT_LENGTH - 1] + "…"
    return value


def _stanza_id(context_info: Any) -> str | None:
    """The quoted message's id in a contextInfo (WhatsApp calls it stanzaId)."""
    if isinstance(context_info, dict):
        stanza_id = context_info.get("stanzaId")
        if isinstance(stanza_id, str) and stanza_id:
            return stanza_id
    return None


def read_note(message: Any, data_context_info: Any = None) -> IncomingNote | None:
    """The kind, text and quoted id of a message; None when it is nothing to show.

    `message` is `data.message` of a messages.upsert; `data_context_info`
    is `data.contextInfo`, where Evolution 2.3.7 puts the reply's quote.
    """
    if not isinstance(message, dict):
        return None
    message = _unwrap(message)
    content_keys = [key for key in message if key not in NOT_A_MESSAGE]
    if not content_keys:
        return None
    # A known kind wins over an unknown key that may sit beside it.
    content_key = next((key for key in content_keys if key in KIND_OF_CONTENT_KEY), content_keys[0])
    kind = KIND_OF_CONTENT_KEY.get(content_key, SenderMessageKind.OTHER)
    content = message[content_key]

    text = None
    if kind in KINDS_WITH_TEXT:
        if isinstance(content, str):  # "conversation" is the text itself
            text = clean_text(content)
        elif isinstance(content, dict):
            text = clean_text(content.get("text")) or clean_text(content.get("caption"))
    if kind == SenderMessageKind.TEXT and text is None:
        return None  # an empty text: nothing to show

    quoted = _stanza_id(data_context_info)
    if quoted is None and isinstance(content, dict):
        quoted = _stanza_id(content.get("contextInfo"))
    return IncomingNote(kind=kind, text=text, quoted_message_id=quoted)


def protocol_message_in(message: Any) -> dict[str, Any] | None:
    """The protocolMessage carried by an upserted message (edit or deletion), if any.

    Evolution 2.3.7 sends those as messages.edited instead (read_change on
    its `data`), but another version may still upsert them.
    """
    if not isinstance(message, dict):
        return None
    protocol = message.get("protocolMessage")
    if isinstance(protocol, dict):
        return protocol
    edited = message.get("editedMessage")
    if isinstance(edited, dict) and isinstance(edited.get("message"), dict):
        protocol = edited["message"].get("protocolMessage")
        if isinstance(protocol, dict):
            return protocol
    return None


def read_change(protocol: Any) -> MessageChange | None:
    """An edit or a deletion from a protocolMessage; None for anything else."""
    if not isinstance(protocol, dict):
        return None
    key = protocol.get("key")
    target = key.get("id") if isinstance(key, dict) else None
    if not isinstance(target, str) or not target:
        return None
    change_type = protocol.get("type")
    if change_type in REVOKE_TYPES:
        return MessageChange(message_id=target, deleted=True)
    edited = protocol.get("editedMessage")
    if change_type in EDIT_TYPES or isinstance(edited, dict):
        note = read_note(edited)
        if note is None or note.text is None:
            return None
        return MessageChange(message_id=target, deleted=False, new_text=note.text)
    return None


def deleted_message_id(data: Any) -> str | None:
    """The id in a messages.delete event's data (the key, flat or under "key")."""
    if not isinstance(data, dict):
        return None
    key = data.get("key") if isinstance(data.get("key"), dict) else data
    message_id = key.get("id")
    return message_id if isinstance(message_id, str) and message_id else None
