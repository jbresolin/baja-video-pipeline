"""Realistic Evolution API v2 webhook bodies, for tests and the fake poster.

Shaped like a real "messages.upsert" event for a WhatsApp document,
trimmed of fields we never read (thumbnails, contextInfo...). The media
fields describe an encrypted file on the WhatsApp CDN (design §15); build
one with shared.testing.whatsapp_media_encrypt.
"""

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
) -> dict[str, Any]:
    """A WhatsApp *document* message (the good case: original quality)."""
    media = {
        "url": url,
        "directPath": direct_path,
        "mimetype": mimetype,
        "title": file_name,
        "fileName": file_name,
        **encrypted.as_webhook_fields(),
    }
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


def text_message_payload(text: str = "oi", sender_phone: str = "5511999999999") -> dict[str, Any]:
    """A plain text message: the intake must acknowledge and ignore it."""
    return _upsert_event(
        message={"conversation": text},
        message_type="conversation",
        sender_phone=sender_phone,
        sender_name="Voluntário Teste",
        message_id=None,
        instance="baja-bot",
    )


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
