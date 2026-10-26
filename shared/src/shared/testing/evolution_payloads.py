"""Realistic Evolution API v2 webhook bodies, for tests and the fake poster.

Shaped like a real "messages.upsert" event for a WhatsApp document,
trimmed of fields we never read (thumbnails, media keys, contextInfo...).
"""

import time
import uuid
from typing import Any


def document_message_payload(
    media_url: str,
    file_name: str = "VID_20261025_101500.mp4",
    file_length: int = 1_000_000,
    mimetype: str = "video/mp4",
    sender_phone: str = "5511999999999",
    sender_name: str = "Voluntário Teste",
    message_id: str | None = None,
    instance: str = "baja-bot",
) -> dict[str, Any]:
    """A WhatsApp *document* message (the good case: original quality)."""
    media = {
        # The real URL is WhatsApp's encrypted CDN; we never download it.
        "url": "https://mmg.whatsapp.net/v/t62.7119-24/fake.enc",
        "mimetype": mimetype,
        "title": file_name,
        "fileName": file_name,
        # WhatsApp sends the size as a string of digits.
        "fileLength": str(file_length),
    }
    return _upsert_event(
        message={"documentMessage": media, "mediaUrl": media_url},
        message_type="documentMessage",
        sender_phone=sender_phone,
        sender_name=sender_name,
        message_id=message_id,
        instance=instance,
    )


def video_message_payload(
    media_url: str,
    file_length: int = 1_000_000,
    sender_phone: str = "5511999999999",
    sender_name: str = "Voluntário Teste",
    message_id: str | None = None,
    instance: str = "baja-bot",
) -> dict[str, Any]:
    """A WhatsApp *video* message (recompressed by WhatsApp → low_res job)."""
    media = {
        "url": "https://mmg.whatsapp.net/v/t62.7161-24/fake.enc",
        "mimetype": "video/mp4",
        "fileLength": str(file_length),
        "seconds": 12,
    }
    return _upsert_event(
        message={"videoMessage": media, "mediaUrl": media_url},
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
