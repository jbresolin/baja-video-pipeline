"""The subset of an Evolution API v2 "messages.upsert" webhook that we read.

Evolution sends much more than this; unknown fields are allowed and ignored
(unlike our own API models, which reject them). JSON uses camelCase, so each
field has a snake_case Python name plus an alias with the JSON spelling.

The media itself is not in the webhook. It sits encrypted on the WhatsApp
CDN; the fields below (url/directPath, mediaKey, the two hashes) are what
shared.whatsapp_media needs to download and decrypt it (design §15).
"""

import base64
from dataclasses import dataclass
from typing import Annotated, Any

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, PlainSerializer

from shared.models.enums import ReceivedAs


def decode_whatsapp_binary(value: Any) -> bytes | None:
    """Turn a binary field from the webhook into bytes.

    WhatsApp's keys and hashes are raw bytes. Depending on version and
    settings, Evolution writes them in JSON as base64 text ("kX3a...="), as
    Node's Buffer form {"type": "Buffer", "data": [12, 200, ...]}, or as an
    object keyed by position {"0": 12, "1": 200, ...}. All three are accepted.
    """
    if value is None or isinstance(value, bytes):
        return value
    if isinstance(value, str):
        return base64.b64decode(value, validate=True)
    if isinstance(value, dict):
        if value.get("type") == "Buffer" and isinstance(value.get("data"), list):
            return bytes(value["data"])
        if value and all(key.isdigit() for key in value):
            return bytes(value[key] for key in sorted(value, key=int))
    raise ValueError("expected base64 text or a Buffer-like object")


def encode_whatsapp_binary(value: bytes | None) -> str | None:
    """Standard base64 (with + and /), the shape Evolution itself sends."""
    return None if value is None else base64.b64encode(value).decode()


# Raw bytes in Python; written back to JSON as standard base64 text.
WhatsAppBinary = Annotated[
    bytes | None,
    BeforeValidator(decode_whatsapp_binary),
    PlainSerializer(encode_whatsapp_binary, when_used="json"),
]


class EvolutionModel(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)


class EvolutionMessageKey(EvolutionModel):
    remote_jid: str = Field(alias="remoteJid")
    from_me: bool = Field(default=False, alias="fromMe")
    # WhatsApp message id; our duplicate-detection key.
    id: str
    # Who inside a group (or broadcast) sent it. Baileys sets it only there,
    # never in a one-to-one chat.
    participant: str | None = None

    @property
    def is_group_or_broadcast(self) -> bool:
        """True unless this came from a private chat with the bot (design §3: no groups).

        Groups end in "@g.us"; "status@broadcast" and broadcast lists in
        "@broadcast". A `participant` marks a group even if the JID looks odd.
        """
        return self.remote_jid.endswith(("@g.us", "@broadcast")) or bool(self.participant)


class EvolutionMediaMessage(EvolutionModel):
    """Fields common to WhatsApp documentMessage and videoMessage."""

    # Full CDN URL of the *encrypted* file, e.g. "https://mmg.whatsapp.net/v/t62...".
    url: str | None = None
    # Path part of the same file on the CDN; used when url is missing or untrusted.
    direct_path: str | None = Field(default=None, alias="directPath")
    # 32 random bytes from the sender; every decryption key is derived from it.
    media_key: WhatsAppBinary = Field(default=None, alias="mediaKey")
    # SHA-256 of the encrypted download (ciphertext + 10-byte MAC).
    file_enc_sha256: WhatsAppBinary = Field(default=None, alias="fileEncSha256")
    # SHA-256 of the decrypted file: becomes the job's sha256.
    file_sha256: WhatsAppBinary = Field(default=None, alias="fileSha256")
    # Size of the decrypted file. WhatsApp sends it as a string of digits;
    # pydantic converts it to int.
    file_length: int | None = Field(default=None, alias="fileLength")
    mimetype: str | None = None
    file_name: str | None = Field(default=None, alias="fileName")
    caption: str | None = None


class EvolutionDocumentWithCaption(EvolutionModel):
    """WhatsApp wraps a document that has a caption one level deeper."""

    message: "EvolutionMessage"


class EvolutionMessage(EvolutionModel):
    document_message: EvolutionMediaMessage | None = Field(default=None, alias="documentMessage")
    video_message: EvolutionMediaMessage | None = Field(default=None, alias="videoMessage")
    document_with_caption_message: EvolutionDocumentWithCaption | None = Field(
        default=None, alias="documentWithCaptionMessage"
    )


# EvolutionDocumentWithCaption mentions EvolutionMessage before it existed;
# now that both are defined, let pydantic resolve that reference.
EvolutionDocumentWithCaption.model_rebuild()


class EvolutionMessageData(EvolutionModel):
    key: EvolutionMessageKey
    push_name: str | None = Field(default=None, alias="pushName")
    message: EvolutionMessage
    message_type: str | None = Field(default=None, alias="messageType")
    message_timestamp: int | None = Field(default=None, alias="messageTimestamp")


class EvolutionWebhook(EvolutionModel):
    """Body of POST /intake/evolution-webhook."""

    # We only act on "messages.upsert"; anything else is acknowledged and ignored.
    event: str
    instance: str
    data: EvolutionMessageData
    server_url: str | None = None


# Fields without which the media cannot be downloaded and verified.
REQUIRED_DECRYPTION_FIELDS = ("media_key", "file_enc_sha256", "file_sha256")


@dataclass(frozen=True)
class IncomingMedia:
    """The media found in a webhook, flattened for the intake code."""

    received_as: ReceivedAs
    media: EvolutionMediaMessage

    def missing_decryption_fields(self) -> list[str]:
        """Names of the fields that are absent, so intake can say why it gave up."""
        missing = [name for name in REQUIRED_DECRYPTION_FIELDS if getattr(self.media, name) is None]
        if self.media.url is None and self.media.direct_path is None:
            missing.append("url/direct_path")
        return missing


def find_incoming_video(message: EvolutionMessage) -> IncomingMedia | None:
    """Return the video carried by a WhatsApp message, or None if there is none.

    A document counts only if its mimetype is video/*. A WhatsApp *video*
    message is accepted too, but marked VIDEO so the job gets low_res=True.
    """
    document = message.document_message
    if document is None and message.document_with_caption_message is not None:
        document = message.document_with_caption_message.message.document_message

    if document is not None and (document.mimetype or "").startswith("video/"):
        return IncomingMedia(ReceivedAs.DOCUMENT, document)
    if message.video_message is not None:
        return IncomingMedia(ReceivedAs.VIDEO, message.video_message)
    return None
