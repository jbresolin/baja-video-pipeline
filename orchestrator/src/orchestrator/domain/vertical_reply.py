"""The bot asks for horizontal videos (LIVESTACK-50).

When the first holder's probe report (PUT /jobs/{id}/media-info) shows a
video filmed upright, the bot quotes that video once with the text from
Admin → Configurações (`vertical_video_message`; empty = off), like the
other replies about one video (domain/bot_replies.py). The video itself is
fine: it is rendered with blurred sides by default, so the ✅ reaction it
got at intake stays.

**Once per video.** Only the job's *first* probe report can trigger it:
a second report (a re-rendered proxy, another holder retrying make_proxy)
finds the report already stored and stays silent, and so does a restart,
because the stored report is in the DB. Jobs from before LIVESTACK-50
already have their report, so they never get one. If Evolution refuses,
nothing is retried; the event `vertical_reply_failed` says why.

The quote addresses the message by the phone JID the job stored
(`sender_jid`). For an `@lid` sender the original remoteJid was not stored
on the job, so the quote's key carries the phone JID instead; WhatsApp
matches a quote by message id, so the reply still points at the video.
"""

import logging
import sqlite3
from dataclasses import dataclass
from typing import Any

from orchestrator.context import OrchestratorContext
from orchestrator.domain.bot_replies import VideoMessageRef
from orchestrator.repositories import events as events_repo
from orchestrator.repositories.events import SYSTEM_ACTOR
from shared.framing import is_vertical
from shared.models import ReceivedAs

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PendingVerticalReply:
    """A reply decided inside the media-info transaction, sent after it."""

    job_id: str
    target: VideoMessageRef
    text: str


def quoted_message_from_row(job_row: sqlite3.Row) -> dict[str, Any]:
    """The quote's message body, as intake builds it: type, mimetype and file name."""
    mimetype = job_row["mimetype"] or "video/mp4"
    if job_row["received_as"] == ReceivedAs.VIDEO:
        return {"videoMessage": {"mimetype": mimetype}}
    document: dict[str, Any] = {"mimetype": mimetype}
    if job_row["original_filename"]:
        document["fileName"] = job_row["original_filename"]
    return {"documentMessage": document}


def vertical_reply_for(
    context: OrchestratorContext,
    job_row: sqlite3.Row,
    display_size: tuple[int, int] | None,
) -> PendingVerticalReply | None:
    """The reply to send for this report, or None.

    `job_row` is the job *before* the report is stored; `display_size` is
    the picture the report describes, after the file's rotation note.
    """
    if job_row["media_info_json"] is not None:
        return None  # not the first probe report of this video
    if display_size is None or not is_vertical(*display_size):
        return None
    text = context.admin_settings.current.vertical_video_message
    if not text or not job_row["sender_jid"]:
        return None  # turned off, or not from WhatsApp (a dev test upload)
    target = VideoMessageRef(
        remote_jid=job_row["sender_jid"],
        message_id=job_row["message_id"],
        reply_jid=job_row["sender_jid"],
        quoted_message=quoted_message_from_row(job_row),
    )
    return PendingVerticalReply(job_id=job_row["id"], target=target, text=text)


def send_vertical_reply(context: OrchestratorContext, reply: PendingVerticalReply) -> None:
    """Send it (after the DB work is committed) and record the attempt as an event."""
    try:
        accepted = context.evolution.send_text(
            reply.target.reply_jid, reply.text, quoted=reply.target.quoted
        )
    except Exception:  # noqa: BLE001 - a failed reply must never fail the worker's upload
        logger.exception("vertical-video reply for job %s failed", reply.job_id)
        accepted = False
    with context.database.transaction() as connection:
        now = context.clock.now()
        if accepted:
            events_repo.record_event(
                connection,
                now,
                actor=SYSTEM_ACTOR,
                kind="vertical_reply_sent",
                job_id=reply.job_id,
                reason="vertical video: asked for horizontal next time",
            )
        else:
            events_repo.record_event(
                connection,
                now,
                actor=SYSTEM_ACTOR,
                kind="vertical_reply_failed",
                job_id=reply.job_id,
                reason="Evolution did not accept the reply (see the orchestrator log)",
            )
