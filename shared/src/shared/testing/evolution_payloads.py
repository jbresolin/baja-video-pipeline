"""Realistic Evolution API v2 webhook bodies, for tests and the fake poster.

Shaped like a real "messages.upsert" event for a WhatsApp document,
trimmed of fields we never read (thumbnails, contextInfo...). The media
fields describe an encrypted file on the WhatsApp CDN (design §15); build
one with shared.testing.whatsapp_media_encrypt.
"""

import copy
import time
import uuid
from typing import Any

from shared.testing.whatsapp_media_encrypt import EncryptedMedia

# Shaped like real CDN paths; tests that never download can use these.
EXAMPLE_DIRECT_PATH = "/v/t62.7119-24/30958033_123456789_n.enc?ccb=11-4&oh=01_Q5AaI&oe=67A1B2C3"
EXAMPLE_URL = "https://mmg.whatsapp.net" + EXAMPLE_DIRECT_PATH + "&mms3=true"

# Placeholder keys/hashes for tests that only parse payloads (32 bytes each).
PLACEHOLDER_MEDIA = EncryptedMedia(
    media_key=bytes(range(32)),
    file_sha256=bytes(32),
    file_enc_sha256=bytes(32),
    file_length=1_000_000,
)


def document_message_payload(
    encrypted: EncryptedMedia = PLACEHOLDER_MEDIA,
    url: str | None = EXAMPLE_URL,
    direct_path: str | None = EXAMPLE_DIRECT_PATH,
    file_name: str = "VID_20261025_101500.mp4",
    mimetype: str = "video/mp4",
    sender_phone: str = "5511999999999",
    sender_name: str = "Voluntário Teste",
    message_id: str | None = None,
    instance: str = "baja-bot",
    caption: str | None = None,
) -> dict[str, Any]:
    """A WhatsApp *document* message (the good case: original quality).

    With a `caption`, the document carries it, as Evolution 2.3.7 sends it
    (prepareMessage flattens WhatsApp's documentWithCaptionMessage).
    """
    media = {
        "url": url,
        "directPath": direct_path,
        "mimetype": mimetype,
        "title": file_name,
        "fileName": file_name,
        **encrypted.as_webhook_fields(),
    }
    if caption is not None:
        media["caption"] = caption
    return _upsert_event(
        message={"documentMessage": media},
        message_type="documentMessage",
        sender_phone=sender_phone,
        sender_name=sender_name,
        message_id=message_id,
        instance=instance,
    )


def video_message_payload(
    encrypted: EncryptedMedia = PLACEHOLDER_MEDIA,
    url: str | None = EXAMPLE_URL,
    direct_path: str | None = EXAMPLE_DIRECT_PATH,
    sender_phone: str = "5511999999999",
    sender_name: str = "Voluntário Teste",
    message_id: str | None = None,
    instance: str = "baja-bot",
) -> dict[str, Any]:
    """A WhatsApp *video* message (recompressed by WhatsApp → low_res job).

    Its keys come from "WhatsApp Video Keys", so encrypt with media_type="video".
    """
    media = {
        "url": url,
        "directPath": direct_path,
        "mimetype": "video/mp4",
        "seconds": 12,
        **encrypted.as_webhook_fields(),
    }
    return _upsert_event(
        message={"videoMessage": media},
        message_type="videoMessage",
        sender_phone=sender_phone,
        sender_name=sender_name,
        message_id=message_id,
        instance=instance,
    )


def text_message_payload(
    text: str = "oi",
    sender_phone: str = "5511999999999",
    *,
    message_id: str | None = None,
    sender_name: str = "Voluntário Teste",
    quoted_message_id: str | None = None,
) -> dict[str, Any]:
    """A text message: no job; kept for the job page (LIVESTACK-49).

    With `quoted_message_id` it is a reply ("swipe to reply" on that
    message). Evolution 2.3.7 sends a reply the way prepareMessage leaves
    it: WhatsApp's extendedTextMessage rewritten into `conversation`, and
    its contextInfo (with the quoted id in `stanzaId`) moved up to
    `data.contextInfo`.
    """
    payload = _upsert_event(
        message={"conversation": text, "messageContextInfo": {"messageSecret": "c2VjcmV0"}},
        message_type="conversation",
        sender_phone=sender_phone,
        sender_name=sender_name,
        message_id=message_id,
        instance="baja-bot",
    )
    if quoted_message_id is not None:
        payload["data"]["contextInfo"] = {
            "stanzaId": quoted_message_id,
            "participant": f"{sender_phone}@s.whatsapp.net",
            "quotedMessage": {"documentMessage": {"mimetype": "video/mp4"}},
        }
    return payload


def media_note_payload(
    kind_key: str,
    sender_phone: str = "5511999999999",
    *,
    caption: str | None = None,
    message_id: str | None = None,
) -> dict[str, Any]:
    """A message that is not a video and not text: "audioMessage" (a voice
    note), "imageMessage", "stickerMessage", "locationMessage"... Only the
    fields the intake could read are filled; it never downloads these."""
    content: dict[str, Any] = {"url": EXAMPLE_URL, "mimetype": "application/octet-stream"}
    if kind_key == "audioMessage":
        content = {
            "url": EXAMPLE_URL,
            "mimetype": "audio/ogg; codecs=opus",
            "seconds": 7,
            "ptt": True,
        }
    if kind_key == "locationMessage":
        content = {"degreesLatitude": -23.5, "degreesLongitude": -46.6}
    if caption is not None:
        content["caption"] = caption
    return _upsert_event(
        message={kind_key: content},
        message_type=kind_key,
        sender_phone=sender_phone,
        sender_name="Voluntário Teste",
        message_id=message_id,
        instance="baja-bot",
    )


def edited_event_payload(
    edited_message_id: str,
    new_text: str | None,
    *,
    sender_phone: str = "5511999999999",
    type_as_name: bool = False,
) -> dict[str, Any]:
    """Evolution 2.3.7's "messages.edited" event: `data` is WhatsApp's protocolMessage.

    `new_text` None means a deletion for everyone (type REVOKE, 0); else an
    edit (type MESSAGE_EDIT, 14) with the new text. Baileys may write the
    type as its number or, through protobuf's toJSON, as its name:
    `type_as_name` picks the second. The key is the changed message's,
    seen from its sender's side (fromMe true). No pushName, no sender key.
    """
    protocol: dict[str, Any] = {
        "key": {
            "remoteJid": f"{sender_phone}@s.whatsapp.net",
            "fromMe": True,
            "id": edited_message_id,
        },
        "timestampMs": str(int(time.time() * 1000)),
    }
    if new_text is None:
        protocol["type"] = "REVOKE" if type_as_name else 0
    else:
        protocol["type"] = "MESSAGE_EDIT" if type_as_name else 14
        protocol["editedMessage"] = {"conversation": new_text}
    return _event("messages.edited", protocol)


def delete_event_payload(
    deleted_message_id: str, sender_phone: str = "5511999999999"
) -> dict[str, Any]:
    """Evolution 2.3.7's "messages.delete" event: the deleted message's key,
    flat, plus "status": "DELETED" (sent only when Evolution has the
    message in its own store)."""
    return _event(
        "messages.delete",
        {
            "remoteJid": f"{sender_phone}@s.whatsapp.net",
            "fromMe": False,
            "id": deleted_message_id,
            "status": "DELETED",
        },
    )


def _event(event: str, data: dict[str, Any]) -> dict[str, Any]:
    return {
        "event": event,
        "instance": "baja-bot",
        "data": data,
        "destination": "http://orchestrator:8000/intake/evolution-webhook",
        "date_time": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()),
        "sender": "5511900000000@s.whatsapp.net",
        "server_url": "http://evolution:8080",
    }


def _upsert_event(
    message: dict[str, Any],
    message_type: str,
    sender_phone: str,
    sender_name: str,
    message_id: str | None,
    instance: str,
) -> dict[str, Any]:
    return {
        "event": "messages.upsert",
        "instance": instance,
        "data": {
            "key": {
                "remoteJid": f"{sender_phone}@s.whatsapp.net",
                "fromMe": False,
                # Real ids look like "3EB0C431C26A1916E5AB"; any unique text works.
                "id": message_id or uuid.uuid4().hex[:20].upper(),
            },
            "pushName": sender_name,
            "message": message,
            "messageType": message_type,
            "messageTimestamp": int(time.time()),
        },
        "destination": "http://orchestrator:8000/intake/evolution-webhook",
        "date_time": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()),
        "sender": "5511900000000@s.whatsapp.net",
        "server_url": "http://evolution:8080",
    }


def as_evolution_2_3_7_lid_payload(
    payload: dict[str, Any], lid: str = "53880532508775"
) -> dict[str, Any]:
    """Reshape a payload the way Evolution 2.3.7 really sent it on 2026-11-04.

    Two differences from the classic shape, both seen in production:
      * the sender is addressed by an anonymous LID ("...@lid") and the phone
        JID moves to key.remoteJidAlt (key.participant is an empty string);
      * WhatsApp's 64-bit numbers are Baileys "Long" objects
        ({"low", "high", "unsigned"}) instead of plain numbers.
    Returns a new dict; the input is not changed.
    """
    reshaped = copy.deepcopy(payload)
    key = reshaped["data"]["key"]
    phone_jid = key["remoteJid"]
    key.update(
        {
            "remoteJid": f"{lid}@lid",
            "remoteJidAlt": phone_jid,
            "addressingMode": "lid",
            "participant": "",
        }
    )
    for media in reshaped["data"]["message"].values():
        if isinstance(media, dict) and "fileLength" in media:
            media["fileLength"] = {"low": int(media["fileLength"]), "high": 0, "unsigned": True}
            media["mediaKeyTimestamp"] = {"low": 1793844688, "high": 0, "unsigned": False}
    return reshaped
