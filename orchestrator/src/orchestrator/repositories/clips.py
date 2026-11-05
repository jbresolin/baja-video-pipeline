"""SQL for clip_versions (every rendering of a clip) and worker_clips (who keeps which)."""

import sqlite3
from collections.abc import Iterable
from datetime import datetime

from orchestrator.db.database import to_db_time


def list_versions(connection: sqlite3.Connection, job_id: str) -> list[sqlite3.Row]:
    """A job's clip versions, oldest first."""
    return connection.execute(
        "SELECT * FROM clip_versions WHERE job_id = ? ORDER BY version", (job_id,)
    ).fetchall()


def get_version_by_sha(
    connection: sqlite3.Connection, job_id: str, sha256: str
) -> sqlite3.Row | None:
    return connection.execute(
        "SELECT * FROM clip_versions WHERE job_id = ? AND sha256 = ?", (job_id, sha256)
    ).fetchone()


def insert_version(
    connection: sqlite3.Connection,
    job_id: str,
    sha256: str,
    size_bytes: int,
    relative_path: str,
    rendered_by: str | None,
    now: datetime,
) -> int:
    """Add the next version of a job's clip and return its number."""
    row = connection.execute(
        "SELECT COALESCE(MAX(version), 0) + 1 AS next FROM clip_versions WHERE job_id = ?",
        (job_id,),
    ).fetchone()
    version = row["next"]
    connection.execute(
        "INSERT INTO clip_versions (job_id, version, sha256, size_bytes, relative_path,"
        " rendered_by, rendered_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (job_id, version, sha256, size_bytes, relative_path, rendered_by, to_db_time(now)),
    )
    return version


def mark_archived(connection: sqlite3.Connection, job_id: str, sha256: str, now: datetime) -> None:
    connection.execute(
        "UPDATE clip_versions SET archived_at = COALESCE(archived_at, ?)"
        " WHERE job_id = ? AND sha256 = ?",
        (to_db_time(now), job_id, sha256),
    )


def mark_archive_failed(connection: sqlite3.Connection, job_id: str, now: datetime) -> None:
    connection.execute(
        "UPDATE clip_versions SET archive_failed_at = ? WHERE job_id = ? AND archived_at IS NULL",
        (to_db_time(now), job_id),
    )


def list_jobs_with_unarchived_clips(connection: sqlite3.Connection) -> list[sqlite3.Row]:
    """Jobs (not discarded) with at least one version the master lacks, oldest render first."""
    return connection.execute(
        """
        SELECT jobs.* FROM jobs
        WHERE jobs.state != 'discarded'
          AND EXISTS (SELECT 1 FROM clip_versions v
                      WHERE v.job_id = jobs.id AND v.archived_at IS NULL)
        ORDER BY (SELECT MIN(v.rendered_at) FROM clip_versions v
                  WHERE v.job_id = jobs.id AND v.archived_at IS NULL), jobs.id
        """
    ).fetchall()


def count_versions(connection: sqlite3.Connection) -> tuple[int, int]:
    """(archived, total) over every clip version of jobs that were not discarded."""
    row = connection.execute(
        "SELECT COUNT(v.archived_at) AS archived, COUNT(*) AS total"
        " FROM clip_versions v JOIN jobs ON jobs.id = v.job_id WHERE jobs.state != 'discarded'"
    ).fetchone()
    return row["archived"], row["total"]


def is_sha_archived(connection: sqlite3.Connection, sha256: str) -> bool:
    return (
        connection.execute(
            "SELECT 1 FROM clip_versions WHERE sha256 = ? AND archived_at IS NOT NULL", (sha256,)
        ).fetchone()
        is not None
    )


# --- worker_clips ---


def replace_worker_clips(
    connection: sqlite3.Connection, worker_id: str, clips: Iterable[tuple[str, str]]
) -> None:
    """Store exactly the (job_id, sha256) pairs the worker reported."""
    connection.execute("DELETE FROM worker_clips WHERE worker_id = ?", (worker_id,))
    connection.executemany(
        "INSERT OR IGNORE INTO worker_clips (worker_id, job_id, sha256) VALUES (?, ?, ?)",
        [(worker_id, job_id, sha256) for job_id, sha256 in clips],
    )


def list_worker_clip_shas(connection: sqlite3.Connection, worker_id: str) -> list[str]:
    rows = connection.execute(
        "SELECT sha256 FROM worker_clips WHERE worker_id = ? ORDER BY sha256", (worker_id,)
    ).fetchall()
    return [row["sha256"] for row in rows]


def list_workers_keeping(connection: sqlite3.Connection, sha256: str) -> list[str]:
    rows = connection.execute(
        "SELECT worker_id FROM worker_clips WHERE sha256 = ? ORDER BY worker_id", (sha256,)
    ).fetchall()
    return [row["worker_id"] for row in rows]
