"""Duplicate videos: the same file sent twice, or forwarded by someone else (LIVESTACK-51 item 3).

Until now only the same WhatsApp *message* was recognised (a webhook
retry); the same video in a new message became a second job. Now a video
is compared by its content:

* **The SHA-256 of the raw.** For a WhatsApp video it is the message's
  `fileSha256`, which the download already checks the decrypted file
  against (shared.whatsapp_media), so a job's `sha256` *is* its
  `fileSha256`. A dev test upload has no `fileSha256`: its SHA-256 is
  computed while it is stored. Either way the one column is compared.
* **Against jobs that are not discarded.** A copy of a discarded video is
  a new video, not a duplicate (the first one was thrown away).
* **When the job is created**, inside the same transaction, so two copies
  arriving at the same moment still make one original and one duplicate.

The VPS still downloads the copy (WhatsApp's CDN, not a worker): a
duplicate is an ordinary job, discarded at once with the reason "duplicate
of <first job>", so a reviewer can restore it like any discard (the same
file may show two cars passing). Workers never download a discarded job;
after the discard grace period the inbox copy goes like any other.

Not detected: a copy re-encoded on the way (different bytes), e.g. a
video WhatsApp recompressed. Comparing pictures is out of scope.
"""

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta

from orchestrator.db.database import from_db_time, to_db_time
from orchestrator.domain import contacts
from orchestrator.domain.bot_replies import REACTION_WARNING, BotReply, VideoMessageRef
from orchestrator.domain.transitions import move_job
from orchestrator.repositories import contacts as contacts_repo
from orchestrator.repositories import jobs as jobs_repo
from orchestrator.repositories.events import SYSTEM_ACTOR
from shared.duplicate_video_message import fill_duplicate_video_message
from shared.models import DuplicateCopy, JobState


@dataclass(frozen=True)
class DuplicateFound:
    """The new job was a copy of `original_id`, received at `original_received_at`."""

    original_id: str
    original_received_at: datetime


def find_original(connection: sqlite3.Connection, sha256: str, job_id: str) -> sqlite3.Row | None:
    """The first job with this raw that is not discarded (and is not `job_id`), or None."""
    return connection.execute(
        "SELECT * FROM jobs WHERE sha256 = ? AND id != ? AND state != ?"
        " ORDER BY created_at, id LIMIT 1",
        (sha256, job_id, str(JobState.DISCARDED)),
    ).fetchone()


def discard_if_duplicate(
    connection: sqlite3.Connection, job_id: str, now: datetime, discard_grace: timedelta
) -> DuplicateFound | None:
    """Discard the just-created job `job_id` when an earlier job has the same raw.

    The move is the machine's (no review version bump). The grace period
    is the Settings value, as for a reviewer's discard (domain/discard.py).
    """
    job_row = jobs_repo.get_job_row(connection, job_id)
    original = find_original(connection, job_row["sha256"], job_id)
    if original is None:
        return None
    move_job(
        connection,
        job_row,
        JobState.DISCARDED,
        now,
        actor=SYSTEM_ACTOR,
        reason=f"duplicate of job {original['id']}: the same video was already received",
        changes={
            "duplicate_of": original["id"],
            "discarded_from": str(JobState.RECEIVED),
            "discard_grace_ends_at": to_db_time(now + discard_grace),
        },
    )
    received_at = from_db_time(original["received_at"] or original["created_at"])
    return DuplicateFound(original_id=original["id"], original_received_at=received_at)


def duplicate_reply(
    target: VideoMessageRef, found: DuplicateFound, template: str, now: datetime
) -> BotReply:
    """⚠️ on the copy, and the Settings text quoting it (none when the text is empty)."""
    texts = ()
    if template:
        texts = (fill_duplicate_video_message(template, found.original_received_at, now),)
    return BotReply(target, REACTION_WARNING, texts)


def duplicates_of(connection: sqlite3.Connection, job_id: str) -> list[DuplicateCopy]:
    """Every copy of this job (Job.duplicates), oldest first, named like the queue does."""
    rows = connection.execute(
        "SELECT id, state, sender_jid, sender_name, received_at, created_at FROM jobs"
        " WHERE duplicate_of = ? ORDER BY created_at, id",
        (job_id,),
    ).fetchall()
    if not rows:
        return []
    names = contacts_repo.display_names(connection)
    copies = []
    for row in rows:
        phone = contacts.sender_phone(row["sender_jid"])
        copies.append(
            DuplicateCopy(
                job_id=row["id"],
                state=JobState(row["state"]),
                sender_name=(names.get(phone) if phone else None) or row["sender_name"],
                received_at=from_db_time(row["received_at"] or row["created_at"]),
            )
        )
    return copies
