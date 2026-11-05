"""Discard and restore: a discard can be undone (LIVESTACK-38 item 4).

Discarding no longer deletes anything right away. For the discard grace
period (Settings page discard_grace_hours, default 24 h from the discard; the end is
stored on the job as discard_grace_ends_at) the holders keep the raw and
the VPS keeps its inbox copy, so "restore" simply puts the job back where
it was.

After the grace period:
* heartbeats list the job in `deletable_job_ids`, so holders delete it;
* the inbox cleanup drops the VPS copy;
* "restore" still works while any copy is left (a confirmed holder that
  has not deleted it yet, e.g. one that was offline, or the inbox), and
  is refused with 409 raw_unavailable once nothing is left.
"""

import sqlite3
from datetime import datetime

from orchestrator.context import OrchestratorContext
from orchestrator.db.database import from_db_time, to_db_time
from orchestrator.domain.raw_copies import confirmed_holder_count
from orchestrator.domain.transitions import move_job, revoke_job_leases
from orchestrator.errors import ApiError
from orchestrator.storage import delete_file_and_partials
from shared.models import ErrorCode, JobState


def discard_grace_ends_at(job_row: sqlite3.Row) -> datetime | None:
    """When a discarded job's raws may go; None for a job that is not discarded."""
    if job_row["state"] != JobState.DISCARDED:
        return None
    return from_db_time(job_row["discard_grace_ends_at"])


def discard_grace_is_over(job_row: sqlite3.Row, now: datetime) -> bool:
    ends_at = discard_grace_ends_at(job_row)
    return ends_at is not None and now >= ends_at


def raw_copy_is_left(connection: sqlite3.Connection, job_row: sqlite3.Row) -> bool:
    """Some machine still has the raw: a confirmed holder, or the VPS inbox."""
    return bool(job_row["raw_on_vps"]) or confirmed_holder_count(connection, job_row["id"]) > 0


def discard_job(
    context: OrchestratorContext,
    connection: sqlite3.Connection,
    job_row: sqlite3.Row,
    changes: dict,
    actor: str,
) -> None:
    """Move to discarded, remembering where from. Raws stay for the grace period."""
    now = context.clock.now()
    job_id = job_row["id"]
    revoke_job_leases(connection, job_id, now, actor=actor, reason="job discarded")
    delete_file_and_partials(context.layout.outbox_path(job_id))
    move_job(
        connection,
        job_row,
        JobState.DISCARDED,
        now,
        actor=actor,
        reason="discarded",
        changes={
            **changes,
            "proxy_requested": 0,
            "waiting_reason": None,
            "discarded_from": job_row["state"],
            "discard_grace_ends_at": to_db_time(now + context.admin_settings.discard_grace),
        },
        bump_version=True,
    )


def restore_job(
    context: OrchestratorContext,
    connection: sqlite3.Connection,
    job_row: sqlite3.Row,
    changes: dict,
    actor: str,
) -> JobState:
    """Undo a discard: back to Job.discarded_from. Returns the state it went to."""
    now = context.clock.now()
    if discard_grace_is_over(job_row, now) and not raw_copy_is_left(connection, job_row):
        raise ApiError(
            409,
            ErrorCode.RAW_UNAVAILABLE,
            "the discard grace period is over and no worker still has the raw",
            {"discard_grace_ends_at": job_row["discard_grace_ends_at"]},
        )
    # Jobs discarded before discarded_from existed were awaiting review or
    # ready; awaiting_review is the safe choice (a reviewer looks again).
    target = JobState(job_row["discarded_from"] or JobState.AWAITING_REVIEW)
    move_job(
        connection,
        job_row,
        target,
        now,
        actor=actor,
        reason="restored after discard",
        changes={
            **changes,
            "discarded_from": None,
            "discard_grace_ends_at": None,
            "finished_at": None,
        },
        bump_version=True,
    )
    return target
