"""The subset of an Evolution API v2 "messages.upsert" webhook that we read.

Evolution sends much more than this; unknown fields are allowed and ignored
(unlike our own API models, which reject them). JSON uses camelCase, so each
field has a snake_case Python name plus an alias with the JSON spelling.
"""

from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field

from shared.models.enums import ReceivedAs


class EvolutionModel(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)


class EvolutionMessageKey(EvolutionModel):
    remote_jid: str = Field(alias="remoteJid")
    from_me: bool = Field(default=False, alias="fromMe")
    # WhatsApp message id; our duplicate-detection key.
    id: str


class EvolutionMediaMessage(EvolutionModel):
    """Fields common to WhatsApp documentMessage and videoMessage."""

    # Encrypted WhatsApp CDN URL. Not directly downloadable; see media_url.
    url: str | None = None
    mimetype: str | None = None
    file_name: str | None = Field(default=None, alias="fileName")
    # WhatsApp sends this as a string of digits; pydantic converts it to int.
    file_length: int | None = Field(default=None, alias="fileLength")
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
    # Plain, decrypted download URL. Evolution adds it when its S3/MinIO media
    # storage is enabled; the orchestrator downloads the raw from here.
    media_url: str | None = Field(default=None, alias="mediaUrl")


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


@dataclass(frozen=True)
class IncomingMedia:
    """The media found in a webhook, flattened for the intake code."""

    received_as: ReceivedAs
    media: EvolutionMediaMessage
    media_url: str | None


def find_incoming_video(message: EvolutionMessage) -> IncomingMedia | None:
    """Return the video carried by a WhatsApp message, or None if there is none.

    A document counts only if its mimetype is video/*. A WhatsApp *video*
    message is accepted too, but marked VIDEO so the job gets low_res=True.
    """
    document = message.document_message
    if document is None and message.document_with_caption_message is not None:
        document = message.document_with_caption_message.message.document_message

    if document is not None and (document.mimetype or "").startswith("video/"):
        return IncomingMedia(ReceivedAs.DOCUMENT, document, message.media_url)
    if message.video_message is not None:
        return IncomingMedia(ReceivedAs.VIDEO, message.video_message, message.media_url)
    return None
