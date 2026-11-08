"""SQL for the jobs and intake_messages tables.

Rows come back as sqlite3.Row (read columns by name: row["state"]).
Turning a row into the API's Job model is domain/job_view.py's job.
"""

import sqlite3
from collections.abc import Iterable
from datetime import datetime
from typing import Any

from orchestrator.db.database import to_db_time
from shared.models import JobState

# Columns update_job may change. A fixed list, because the names are put
# into the SQL text (values never are; they are always "?" parameters).
UPDATABLE_JOB_COLUMNS = frozenset(
    {
        "state", "finished_at", "duration_s", "has_audio", "frame_rate", "raw_on_vps",
        "media_info_json", "discarded_from", "discard_grace_ends_at",
        "proxy_available", "thumbnail_available", "proxy_requested",
        "prova", "car_number", "team_name", "overlay_team_name", "overlay_school_short",
        "school_name", "recorded_at", "recorded_at_source",
        "passagem", "show_pass",
        "cut_in_s", "cut_out_s", "reviewed_by", "reviewed_at",
        "clip_sha256", "clip_size_bytes", "clip_relative_path", "clip_route",
        "clip_rendered_by", "clip_delivered_at",
        "failure_worker_id", "failure_task_kind", "failure_message",
        "failure_exit_code", "failure_at", "failed_from", "waiting_reason",
        "claimed_render_inputs_json", "reported_render_inputs_json",
        "display_width", "display_height", "rotation", "framing", "crop_position", "no_audio",
        "show_pass_result", "largada_s", "show_clock", "duplicate_of",
    }
)  # fmt: skip


def insert_job(connection: sqlite3.Connection, columns: dict[str, Any]) -> None:
    names = ", ".join(columns)
    placeholders = ", ".join("?" for _ in columns)
    connection.execute(
        f"INSERT INTO jobs ({names}) VALUES ({placeholders})", list(columns.values())
    )


def get_job_row(connection: sqlite3.Connection, job_id: str) -> sqlite3.Row | None:
    return connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()


def update_job(
    connection: sqlite3.Connection,
    job_id: str,
    now: datetime,
    changes: dict[str, Any],
    *,
    bump_version: bool = False,
) -> None:
    """Change some columns of one job and stamp updated_at.

    `version` is the job's *review* version (docs/api.md §4): it goes up
    by one only when bump_version=True, which only reviewer actions pass
    (domain/review.py). Machine activity (holder confirmations, proxy and
    thumbnail uploads, leases, sweepers) never bumps it, so a worker busy
    with the job cannot make a reviewer's save fail with version_conflict.
    """
    unknown = set(changes) - UPDATABLE_JOB_COLUMNS
    if unknown:
        raise ValueError(f"update_job cannot change {sorted(unknown)}")
    assignments = [f"{column} = ?" for column in changes]
    values = list(changes.values())
    assignments.append("updated_at = ?")
    values.append(to_db_time(now))
    if bump_version:
        assignments.append("version = version + 1")
    values.append(job_id)
    connection.execute(f"UPDATE jobs SET {', '.join(assignments)} WHERE id = ?", values)


def list_job_rows_in_states(
    connection: sqlite3.Connection, states: Iterable[JobState], limit: int | None = None
) -> list[sqlite3.Row]:
    """Jobs in any of `states`, oldest first (the order work and review are handed out in)."""
    state_list = [str(state) for state in states]
    if not state_list:
        return []
    placeholders = ", ".join("?" for _ in state_list)
    sql = f"SELECT * FROM jobs WHERE state IN ({placeholders}) ORDER BY created_at, id"
    parameters: list[Any] = list(state_list)
    if limit is not None:
        sql += " LIMIT ?"
        parameters.append(limit)
    return connection.execute(sql, parameters).fetchall()


def list_review_queue_rows(
    connection: sqlite3.Connection, states: Iterable[JobState], limit: int | None = None
) -> list[sqlite3.Row]:
    """The review queue: jobs in `states` in filming order (LIVESTACK-45).

    Oldest recording first (recorded_at), ties by arrival. A job without a
    recording time (none should have one; see migration 0011) sorts by
    arrival among the others' recording times.
    """
    state_list = [str(state) for state in states]
    if not state_list:
        return []
    placeholders = ", ".join("?" for _ in state_list)
    sql = (
        f"SELECT * FROM jobs WHERE state IN ({placeholders})"
        " ORDER BY COALESCE(recorded_at, created_at), created_at, id"
    )
    parameters: list[Any] = list(state_list)
    if limit is not None:
        sql += " LIMIT ?"
        parameters.append(limit)
    return connection.execute(sql, parameters).fetchall()


def list_car_job_rows(connection: sqlite3.Connection, car_number: int) -> list[sqlite3.Row]:
    """Every job of one car that has a prova and is not discarded, in filming order.

    The caller keeps those of one prova (by clip folder, domain/car_videos.py).
    """
    return connection.execute(
        "SELECT * FROM jobs WHERE car_number = ? AND prova IS NOT NULL AND state != 'discarded'"
        " ORDER BY COALESCE(recorded_at, created_at), created_at, id",
        (car_number,),
    ).fetchall()


def list_job_rows_with_media_info(connection: sqlite3.Connection) -> list[sqlite3.Row]:
    return connection.execute(
        "SELECT * FROM jobs WHERE media_info_json IS NOT NULL ORDER BY created_at, id"
    ).fetchall()


def set_recorded_at(connection: sqlite3.Connection, job_id: str, columns: dict[str, str]) -> None:
    """Store a recording time worked out again later (startup backfill).

    Not through update_job: nobody changed the job, so updated_at stays.
    """
    connection.execute(
        "UPDATE jobs SET recorded_at = ?, recorded_at_source = ? WHERE id = ?",
        (columns["recorded_at"], columns["recorded_at_source"], job_id),
    )


def set_display_size(
    connection: sqlite3.Connection, job_id: str, width: int | None, height: int | None
) -> None:
    """Store a picture size worked out again later (startup backfill); updated_at stays."""
    connection.execute(
        "UPDATE jobs SET display_width = ?, display_height = ? WHERE id = ?",
        (width, height, job_id),
    )


def list_job_rows_with_raw_on_vps(connection: sqlite3.Connection) -> list[sqlite3.Row]:
    return connection.execute(
        "SELECT * FROM jobs WHERE raw_on_vps = 1 ORDER BY created_at"
    ).fetchall()


def list_job_rows_needing_proxy(connection: sqlite3.Connection) -> list[sqlite3.Row]:
    """Jobs a make_proxy task could be offered for: no proxy yet, or one was requested."""
    return connection.execute(
        "SELECT * FROM jobs WHERE state NOT IN ('discarded', 'failed')"
        " AND (state = 'received' OR proxy_requested = 1) ORDER BY created_at, id"
    ).fetchall()


def list_delivered_job_rows(connection: sqlite3.Connection) -> list[sqlite3.Row]:
    """Jobs whose current clip reached the studio, newest delivery first."""
    return connection.execute(
        "SELECT * FROM jobs WHERE state IN ('delivered', 'done')"
        " AND clip_delivered_at IS NOT NULL ORDER BY clip_delivered_at DESC, id"
    ).fetchall()


def list_finished_job_rows_with_proxy(connection: sqlite3.Connection) -> list[sqlite3.Row]:
    """Proxy eviction candidates: done or discarded jobs, oldest finished first."""
    return connection.execute(
        "SELECT * FROM jobs WHERE state IN ('done', 'discarded') AND proxy_available = 1"
        " ORDER BY finished_at, created_at"
    ).fetchall()


def count_jobs_by_state(connection: sqlite3.Connection) -> dict[str, int]:
    rows = connection.execute("SELECT state, COUNT(*) AS n FROM jobs GROUP BY state").fetchall()
    return {row["state"]: row["n"] for row in rows}


# --- intake_messages: one row per accepted WhatsApp message ---


def get_intake_message(connection: sqlite3.Connection, message_id: str) -> sqlite3.Row | None:
    return connection.execute(
        "SELECT * FROM intake_messages WHERE message_id = ?", (message_id,)
    ).fetchone()


def save_intake_message(
    connection: sqlite3.Connection,
    message_id: str,
    status: str,
    now: datetime,
    *,
    job_id: str | None = None,
    sender_jid: str | None = None,
    detail: str | None = None,
) -> None:
    """Insert the message, or update its status if it is already known."""
    connection.execute(
        """
        INSERT INTO intake_messages
            (message_id, status, job_id, sender_jid, created_at, updated_at, detail)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (message_id) DO UPDATE SET
            status = excluded.status,
            job_id = COALESCE(excluded.job_id, intake_messages.job_id),
            updated_at = excluded.updated_at,
            detail = excluded.detail
        """,
        (message_id, status, job_id, sender_jid, to_db_time(now), to_db_time(now), detail),
    )


# --- Candidates for /workers/{id}/next. Rough SQL filters; domain/scheduling.py
# applies the exact rules to each candidate. All oldest first. ---


def list_download_candidates(
    connection: sqlite3.Connection, worker_id: str, max_holders: int | None
) -> list[sqlite3.Row]:
    """Live jobs the worker does not hold, with fewer than `max_holders` holders (None: any).

    "Live" means still moving through the pipeline: not done, failed or
    discarded. The master's backfill of finished jobs is a separate, lower
    priority list (list_backfill_candidates).
    """
    return connection.execute(
        """
        SELECT * FROM jobs
        WHERE state NOT IN ('discarded', 'failed', 'done')
          AND NOT EXISTS (SELECT 1 FROM holders h WHERE h.job_id = jobs.id AND h.worker_id = ?)
          AND (? IS NULL OR (SELECT COUNT(*) FROM holders h WHERE h.job_id = jobs.id) < ?)
        ORDER BY created_at, id
        """,
        (worker_id, max_holders, max_holders),
    ).fetchall()


def list_backfill_candidates(connection: sqlite3.Connection, worker_id: str) -> list[sqlite3.Row]:
    """Finished jobs (done or failed) whose raw this worker does not hold, oldest first.

    Design §17: the master must end up holding every raw that was not
    discarded, including jobs that finished while it was offline.
    """
    return connection.execute(
        """
        SELECT * FROM jobs
        WHERE state IN ('done', 'failed')
          AND NOT EXISTS (SELECT 1 FROM holders h WHERE h.job_id = jobs.id AND h.worker_id = ?)
        ORDER BY created_at, id
        """,
        (worker_id,),
    ).fetchall()


def list_jobs_held_by(
    connection: sqlite3.Connection, worker_id: str, states: Iterable[JobState]
) -> list[sqlite3.Row]:
    """Jobs in `states` whose raw this worker holds (confirmed)."""
    state_list = [str(state) for state in states]
    placeholders = ", ".join("?" for _ in state_list)
    return connection.execute(
        f"""
        SELECT jobs.* FROM jobs JOIN holders h ON h.job_id = jobs.id
        WHERE h.worker_id = ? AND h.status = 'confirmed' AND jobs.state IN ({placeholders})
        ORDER BY jobs.created_at, jobs.id
        """,
        [worker_id, *state_list],
    ).fetchall()


def list_outbox_deliver_candidates(connection: sqlite3.Connection) -> list[sqlite3.Row]:
    return connection.execute(
        "SELECT * FROM jobs WHERE state = 'uploaded' AND clip_route = 'outbox'"
        " ORDER BY updated_at, id"
    ).fetchall()


def list_original_request_rows(connection: sqlite3.Connection) -> list[sqlite3.Row]:
    """Jobs a reviewer asked the original of ("Baixar original"), oldest request first."""
    return connection.execute(
        "SELECT * FROM jobs WHERE original_requested_at IS NOT NULL"
        " ORDER BY original_requested_at, id"
    ).fetchall()


def set_original_requested_at(
    connection: sqlite3.Connection, job_id: str, when: datetime | None
) -> None:
    """Remember (or forget, with None) a reviewer's "Baixar original".

    Not through update_job: asking for a download is not a change to the
    job, so updated_at stays (the job page would otherwise think someone
    edited it).
    """
    connection.execute(
        "UPDATE jobs SET original_requested_at = ? WHERE id = ?",
        (to_db_time(when) if when is not None else None, job_id),
    )


# --- Clip previews and "Baixar clipe" (LIVESTACK-52) ---
#
# Not through update_job, like the original requests above: watching or
# downloading a clip is not a change to the job, so updated_at stays.


def set_preview_wanted(connection: sqlite3.Connection, job_id: str, wanted: bool) -> None:
    """At each process claim: did the task ask for a preview (Settings switch on)?"""
    connection.execute(
        "UPDATE jobs SET preview_wanted = ? WHERE id = ?", (1 if wanted else 0, job_id)
    )


def set_preview_clip_sha256(
    connection: sqlite3.Connection, job_id: str, clip_sha256: str | None
) -> None:
    """Which clip the job's preview file shows (None = no preview at all)."""
    connection.execute(
        "UPDATE jobs SET preview_clip_sha256 = ? WHERE id = ?", (clip_sha256, job_id)
    )


def list_job_rows_with_preview(connection: sqlite3.Connection) -> list[sqlite3.Row]:
    """Jobs that have (or had, before an eviction) a clip preview, finished ones
    first and oldest first, the order previews are evicted in."""
    return connection.execute(
        "SELECT * FROM jobs WHERE preview_clip_sha256 IS NOT NULL"
        " ORDER BY state NOT IN ('done', 'discarded'), finished_at, created_at, id"
    ).fetchall()


def list_clip_download_rows(connection: sqlite3.Connection) -> list[sqlite3.Row]:
    """Jobs a reviewer asked the clip of ("Baixar clipe"), oldest request first."""
    return connection.execute(
        "SELECT * FROM jobs WHERE clip_download_requested_at IS NOT NULL"
        " ORDER BY clip_download_requested_at, id"
    ).fetchall()


def set_clip_download_request(
    connection: sqlite3.Connection, job_id: str, when: datetime, clip_sha256: str
) -> None:
    """Remember a reviewer's "Baixar clipe" of that clip; a new request starts over."""
    connection.execute(
        "UPDATE jobs SET clip_download_requested_at = ?, clip_download_sha256 = ?,"
        " clip_download_completed_at = NULL WHERE id = ?",
        (to_db_time(when), clip_sha256, job_id),
    )


def clear_clip_download_request(connection: sqlite3.Connection, job_id: str) -> None:
    """Forget the request (its copy was deleted)."""
    connection.execute(
        "UPDATE jobs SET clip_download_requested_at = NULL, clip_download_sha256 = NULL,"
        " clip_download_completed_at = NULL WHERE id = ?",
        (job_id,),
    )


def set_clip_download_completed(
    connection: sqlite3.Connection, job_id: str, when: datetime
) -> bool:
    """Record the *first* complete download; True when this was it."""
    cursor = connection.execute(
        "UPDATE jobs SET clip_download_completed_at = ? WHERE id = ?"
        " AND clip_download_requested_at IS NOT NULL AND clip_download_completed_at IS NULL",
        (to_db_time(when), job_id),
    )
    return cursor.rowcount == 1
