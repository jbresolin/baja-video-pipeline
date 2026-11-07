"""The sender's messages around a video, for the job page (LIVESTACK-49).

Filmmakers write what the reviewer needs next to the video: "carro 42,
segunda passagem" before or after sending it, or a reply to the video
saying "errei, é a 3". Intake keeps every private message that is not a
video (keep_message), from a contact whose videos would be accepted, and
applies edits and deletions to them (apply_change). Nothing is downloaded
and nothing is answered.

Which messages a video's section lists (section_rows), for one sender:

    video A ── m1 ── m2 ── video B ── m3 ── video C
                                      m4 (quotes video A)

* the replies: messages quoting this video, wherever they fall in time
  (m4 is in A's section, as "resposta a este vídeo");
* after: received after this video and before the sender's next one
  (m1, m2 for A; m3 for B);
* before: received after the sender's previous video and before this
  one (m1, m2 for B), because people often announce the car first;
* a message quoting another video belongs to that one only (m4 is in
  neither B's nor C's section).

So a message between two videos is listed under both, as "after" of the
first and "before" of the second. Every job of the sender counts as a
video here, whatever its state (a discarded video was still sent).
Messages and videos are ordered by when the webhook arrived; a message
received at the same instant as a video counts as after it.
"""

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta

from orchestrator.context import OrchestratorContext
from orchestrator.db.database import from_db_time
from orchestrator.domain.job_view import require_job_row
from orchestrator.domain.whatsapp_notes import IncomingNote, MessageChange
from orchestrator.repositories import sender_messages as messages_repo
from shared.models import (
    SenderMessage,
    SenderMessageKind,
    SenderMessageRole,
    SenderMessagesResponse,
)


def keep_message(
    connection: sqlite3.Connection,
    *,
    message_id: str,
    sender_jid: str,
    note: IncomingNote,
    received_at: datetime,
    sent_at: datetime | None,
) -> bool:
    """Store one message; False when Evolution already delivered it."""
    return messages_repo.insert_message(
        connection,
        message_id=message_id,
        sender_jid=sender_jid,
        received_at=received_at,
        sent_at=sent_at,
        kind=str(note.kind),
        text=note.text,
        quoted_message_id=note.quoted_message_id,
    )


def apply_change(connection: sqlite3.Connection, change: MessageChange, now: datetime) -> bool:
    """An edit replaces the text (marked edited); a deletion drops it (marked deleted).

    Matched by message id only: Evolution's messages.edited event does not
    say who sent the change, and WhatsApp lets people change only their
    own messages. False when the message is not stored (sent before this
    feature, by the bot, a video, or already swept).
    """
    if change.deleted:
        return messages_repo.mark_deleted(connection, change.message_id, now)
    return messages_repo.replace_text(connection, change.message_id, change.new_text, now)


def delete_expired(connection: sqlite3.Connection, now: datetime, keep_days: int) -> int:
    """The retention sweep: delete messages received more than `keep_days` ago."""
    return messages_repo.delete_received_before(connection, now - timedelta(days=keep_days))


@dataclass(frozen=True)
class _Video:
    job_id: str
    message_id: str
    received_at: datetime


def _videos_of(connection: sqlite3.Connection, sender_jid: str) -> list[_Video]:
    return [
        _Video(
            job_id=row["id"],
            message_id=row["message_id"],
            # Jobs from before LIVESTACK-45 got received_at from created_at.
            received_at=from_db_time(row["received_at"] or row["created_at"]),
        )
        for row in messages_repo.list_videos_of_sender(connection, sender_jid)
    ]


def section_rows(
    videos: list[_Video], messages: list[sqlite3.Row], job_id: str
) -> list[tuple[SenderMessageRole, sqlite3.Row]]:
    """This video's messages with their role, in display order (module docstring).

    `videos` and `messages` are all of one sender's, oldest first.
    """
    position = next(index for index, video in enumerate(videos) if video.job_id == job_id)
    this_video = videos[position]
    previous_at = videos[position - 1].received_at if position > 0 else None
    next_at = videos[position + 1].received_at if position + 1 < len(videos) else None
    video_message_ids = {video.message_id for video in videos}

    replies, after, before = [], [], []
    for row in messages:
        quoted = row["quoted_message_id"]
        if quoted == this_video.message_id:
            replies.append(row)
            continue
        if quoted in video_message_ids:
            continue  # a reply to another of the sender's videos belongs there
        received_at = from_db_time(row["received_at"])
        if received_at >= this_video.received_at:
            if next_at is None or received_at < next_at:
                after.append(row)
        elif previous_at is None or received_at >= previous_at:
            before.append(row)
    return (
        [(SenderMessageRole.REPLY, row) for row in replies]
        + [(SenderMessageRole.AFTER, row) for row in after]
        + [(SenderMessageRole.BEFORE, row) for row in before]
    )


def _caption_item(job_row: sqlite3.Row) -> SenderMessage | None:
    if not job_row["caption"]:
        return None
    return SenderMessage(
        message_id=job_row["message_id"],
        role=SenderMessageRole.CAPTION,
        kind=SenderMessageKind.VIDEO,
        text=job_row["caption"],
        received_at=from_db_time(job_row["received_at"] or job_row["created_at"]),
    )


def _item(role: SenderMessageRole, row: sqlite3.Row) -> SenderMessage:
    return SenderMessage(
        message_id=row["message_id"],
        role=role,
        kind=SenderMessageKind(row["kind"]),
        text=row["text"],
        received_at=from_db_time(row["received_at"]),
        edited=row["edited_at"] is not None,
        deleted=row["deleted_at"] is not None,
    )


def section_for_job_row(
    connection: sqlite3.Connection, job_row: sqlite3.Row
) -> list[SenderMessage]:
    """The caption first, then replies, after and before (section_rows)."""
    items = []
    caption = _caption_item(job_row)
    if caption is not None:
        items.append(caption)
    sender_jid = job_row["sender_jid"]
    if sender_jid:  # test uploads have no sender
        videos = _videos_of(connection, sender_jid)
        messages = messages_repo.list_messages_of_sender(connection, sender_jid)
        items += [_item(role, row) for role, row in section_rows(videos, messages, job_row["id"])]
    return items


def sender_messages_for_job(context: OrchestratorContext, job_id: str) -> SenderMessagesResponse:
    """GET /jobs/{job_id}/sender-messages (404 for an unknown job)."""
    with context.database.transaction() as connection:
        job_row = require_job_row(connection, job_id)
        items = section_for_job_row(connection, job_row)
    return SenderMessagesResponse(job_id=job_id, sender_name=job_row["sender_name"], items=items)


def message_counts(connection: sqlite3.Connection, job_rows: list[sqlite3.Row]) -> dict[str, int]:
    """How many messages each job's section lists, for the queue's speech-bubble marker.

    Each sender's videos and messages are read once, however many of their
    jobs the list has.
    """
    counts = {}
    rows_by_sender: dict[str | None, list[sqlite3.Row]] = {}
    for row in job_rows:
        rows_by_sender.setdefault(row["sender_jid"], []).append(row)
    for sender_jid, rows in rows_by_sender.items():
        videos = _videos_of(connection, sender_jid) if sender_jid else []
        messages = (
            messages_repo.list_messages_of_sender(connection, sender_jid) if sender_jid else []
        )
        for row in rows:
            listed = len(section_rows(videos, messages, row["id"])) if sender_jid else 0
            counts[row["id"]] = listed + (1 if row["caption"] else 0)
    return counts
