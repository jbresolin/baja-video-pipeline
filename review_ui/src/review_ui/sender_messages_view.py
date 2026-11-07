"""The job page's "Mensagens de <remetente>" section (LIVESTACK-49).

The orchestrator decides which of the sender's messages belong to this
video and in what order (GET /jobs/{id}/sender-messages: caption, replies,
after, before). This module only shapes that for the template: the
section title with its count, the lines shown open, the ones folded under
"Antes deste vídeo", and whether the section starts open (it does when
someone replied to this very video).
"""

from dataclasses import dataclass
from datetime import datetime

from review_ui.catalog import text
from shared.models import (
    Job,
    SenderMessage,
    SenderMessageKind,
    SenderMessageRole,
    SenderMessagesResponse,
)


@dataclass(frozen=True)
class MessageLine:
    message: SenderMessage
    # "(áudio)", "(imagem)"... for anything but plain text; None for text.
    kind_label: str | None
    # "legenda do vídeo" or "resposta a este vídeo"; None for the others.
    role_label: str | None


@dataclass(frozen=True)
class SenderMessagesSection:
    # "Mensagens de Maria (3)": every message the section holds.
    title: str
    sender: str
    # The caption, the replies and what came after this video.
    lines: list[MessageLine]
    # What came after the sender's previous video, folded by default.
    before: list[MessageLine]
    # Open on arrival when one of the messages is a reply to this video.
    open: bool
    # Times show the date only when not on this video's day.
    reference_time: datetime


ROLE_LABEL_KEYS = {
    SenderMessageRole.CAPTION: "messages.role.caption",
    SenderMessageRole.REPLY: "messages.role.reply",
}


def _line(message: SenderMessage) -> MessageLine:
    kind_label = None
    if message.kind not in (SenderMessageKind.TEXT, SenderMessageKind.VIDEO):
        kind_label = text(f"messages.kind.{message.kind}")
    role_key = ROLE_LABEL_KEYS.get(message.role)
    return MessageLine(
        message=message,
        kind_label=kind_label,
        role_label=text(role_key) if role_key else None,
    )


def sender_messages_section(
    response: SenderMessagesResponse, job: Job
) -> SenderMessagesSection | None:
    """The section for the template; None when the sender said nothing around this video."""
    if not response.items:
        return None
    sender = response.sender_name or job.source.sender_name or text("messages.unknown_sender")
    lines = [_line(item) for item in response.items if item.role != SenderMessageRole.BEFORE]
    before = [_line(item) for item in response.items if item.role == SenderMessageRole.BEFORE]
    return SenderMessagesSection(
        title=text("messages.title", sender=sender, count=len(response.items)),
        sender=sender,
        lines=lines,
        before=before,
        open=any(item.role == SenderMessageRole.REPLY for item in response.items),
        reference_time=job.source.received_at or job.created_at,
    )
