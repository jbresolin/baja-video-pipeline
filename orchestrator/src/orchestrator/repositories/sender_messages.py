"""SQL for the sender_messages table (LIVESTACK-49) and the sender's videos.

Rows come back as sqlite3.Row. Which message belongs to which video is
domain/sender_messages.py's job.
"""

import sqlite3
from datetime import datetime

from orchestrator.db.database import to_db_time


def insert_message(
    connection: sqlite3.Connection,
    *,
    message_id: str,
    sender_jid: str,
    received_at: datetime,
    sent_at: datetime | None,
    kind: str,
    text: str | None,
    quoted_message_id: str | None,
) -> bool:
    """Store one message; False when that id is already stored (Evolution retried)."""
    cursor = connection.execute(
        "INSERT OR IGNORE INTO sender_messages"
        " (message_id, sender_jid, received_at, sent_at, kind, text, quoted_message_id)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            message_id,
            sender_jid,
            to_db_time(received_at),
            to_db_time(sent_at),
            kind,
            text,
            quoted_message_id,
        ),
    )
    return cursor.rowcount == 1


def replace_text(connection: sqlite3.Connection, message_id: str, text: str, now: datetime) -> bool:
    """An edit: the new text, marked edited. Deleted messages stay deleted."""
    cursor = connection.execute(
        "UPDATE sender_messages SET text = ?, edited_at = ?"
        " WHERE message_id = ? AND deleted_at IS NULL",
        (text, to_db_time(now), message_id),
    )
    return cursor.rowcount == 1


def mark_deleted(connection: sqlite3.Connection, message_id: str, now: datetime) -> bool:
    """A deletion for everyone: the text is dropped, not only hidden."""
    cursor = connection.execute(
        "UPDATE sender_messages SET text = NULL, deleted_at = ?"
        " WHERE message_id = ? AND deleted_at IS NULL",
        (to_db_time(now), message_id),
    )
    return cursor.rowcount == 1


def list_messages_of_sender(connection: sqlite3.Connection, sender_jid: str) -> list[sqlite3.Row]:
    """Every stored message of one sender, oldest first (ties in storing order)."""
    return connection.execute(
        "SELECT * FROM sender_messages WHERE sender_jid = ? ORDER BY received_at, rowid",
        (sender_jid,),
    ).fetchall()


def list_videos_of_sender(connection: sqlite3.Connection, sender_jid: str) -> list[sqlite3.Row]:
    """Every job of one sender, discarded ones included, oldest arrival first.

    Only the columns the sections need: a video is a boundary between
    "after the previous one" and "before the next one" whatever its state.
    """
    return connection.execute(
        "SELECT id, message_id, received_at, created_at FROM jobs WHERE sender_jid = ?"
        " ORDER BY COALESCE(received_at, created_at), created_at, id",
        (sender_jid,),
    ).fetchall()


def delete_received_before(connection: sqlite3.Connection, cutoff: datetime) -> int:
    """The retention sweep: how many messages were deleted."""
    cursor = connection.execute(
        "DELETE FROM sender_messages WHERE received_at < ?", (to_db_time(cutoff),)
    )
    return cursor.rowcount
