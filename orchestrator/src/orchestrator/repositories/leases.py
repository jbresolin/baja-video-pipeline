"""SQL for holders, leases and task exclusions."""

import sqlite3
from datetime import datetime, timedelta

from orchestrator.db.database import from_db_time, to_db_time
from shared.models import Holder, HolderStatus, Lease, TaskKind

# --- Holders ---


def list_holders(connection: sqlite3.Connection, job_id: str) -> list[Holder]:
    rows = connection.execute(
        "SELECT * FROM holders WHERE job_id = ? ORDER BY assigned_at, worker_id", (job_id,)
    ).fetchall()
    return [
        Holder(
            worker_id=row["worker_id"],
            status=HolderStatus(row["status"]),
            assigned_at=from_db_time(row["assigned_at"]),
            confirmed_at=from_db_time(row["confirmed_at"]),
        )
        for row in rows
    ]


def add_assigned_holder(
    connection: sqlite3.Connection, job_id: str, worker_id: str, now: datetime
) -> None:
    """Record that a worker started downloading. Keeps an existing confirmed row as is."""
    connection.execute(
        "INSERT INTO holders (job_id, worker_id, status, assigned_at) VALUES (?, ?, ?, ?)"
        " ON CONFLICT (job_id, worker_id) DO NOTHING",
        (job_id, worker_id, str(HolderStatus.ASSIGNED), to_db_time(now)),
    )


def confirm_holder(
    connection: sqlite3.Connection, job_id: str, worker_id: str, now: datetime
) -> None:
    connection.execute(
        "UPDATE holders SET status = ?, confirmed_at = ? WHERE job_id = ? AND worker_id = ?",
        (str(HolderStatus.CONFIRMED), to_db_time(now), job_id, worker_id),
    )


def remove_holder(connection: sqlite3.Connection, job_id: str, worker_id: str) -> None:
    connection.execute(
        "DELETE FROM holders WHERE job_id = ? AND worker_id = ?", (job_id, worker_id)
    )


def list_confirmed_job_ids_for_worker(connection: sqlite3.Connection, worker_id: str) -> list[str]:
    rows = connection.execute(
        "SELECT job_id FROM holders WHERE worker_id = ? AND status = ?",
        (worker_id, str(HolderStatus.CONFIRMED)),
    ).fetchall()
    return [row["job_id"] for row in rows]


def held_bytes_for_worker(connection: sqlite3.Connection, worker_id: str) -> tuple[int, int]:
    """(number of confirmed raws, their total size) held by one worker."""
    row = connection.execute(
        "SELECT COUNT(*) AS n, COALESCE(SUM(jobs.size_bytes), 0) AS total FROM holders"
        " JOIN jobs ON jobs.id = holders.job_id"
        " WHERE holders.worker_id = ? AND holders.status = ?",
        (worker_id, str(HolderStatus.CONFIRMED)),
    ).fetchone()
    return row["n"], row["total"]


# --- Leases ---


def _lease_from_row(row: sqlite3.Row) -> Lease:
    return Lease(
        lease_id=row["lease_id"],
        job_id=row["job_id"],
        worker_id=row["worker_id"],
        task_kind=TaskKind(row["task_kind"]),
        acquired_at=from_db_time(row["acquired_at"]),
        expires_at=from_db_time(row["expires_at"]),
    )


def insert_lease(connection: sqlite3.Connection, lease: Lease) -> None:
    connection.execute(
        "INSERT INTO leases (lease_id, job_id, worker_id, task_kind, acquired_at, expires_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (
            lease.lease_id,
            lease.job_id,
            lease.worker_id,
            str(lease.task_kind),
            to_db_time(lease.acquired_at),
            to_db_time(lease.expires_at),
        ),
    )


def get_lease(connection: sqlite3.Connection, lease_id: str) -> Lease | None:
    row = connection.execute("SELECT * FROM leases WHERE lease_id = ?", (lease_id,)).fetchone()
    return _lease_from_row(row) if row else None


def list_leases_for_job(
    connection: sqlite3.Connection, job_id: str, task_kind: TaskKind | None = None
) -> list[Lease]:
    if task_kind is None:
        rows = connection.execute(
            "SELECT * FROM leases WHERE job_id = ? ORDER BY acquired_at", (job_id,)
        ).fetchall()
    else:
        rows = connection.execute(
            "SELECT * FROM leases WHERE job_id = ? AND task_kind = ? ORDER BY acquired_at",
            (job_id, str(task_kind)),
        ).fetchall()
    return [_lease_from_row(row) for row in rows]


def list_leases_for_worker(connection: sqlite3.Connection, worker_id: str) -> list[Lease]:
    rows = connection.execute(
        "SELECT * FROM leases WHERE worker_id = ? ORDER BY acquired_at", (worker_id,)
    ).fetchall()
    return [_lease_from_row(row) for row in rows]


def list_expired_leases(connection: sqlite3.Connection, now: datetime) -> list[Lease]:
    rows = connection.execute(
        "SELECT * FROM leases WHERE expires_at <= ? ORDER BY expires_at", (to_db_time(now),)
    ).fetchall()
    return [_lease_from_row(row) for row in rows]


def extend_lease(
    connection: sqlite3.Connection, lease_id: str, now: datetime, ttl_s: int
) -> datetime:
    expires_at = now + timedelta(seconds=ttl_s)
    connection.execute(
        "UPDATE leases SET expires_at = ? WHERE lease_id = ?", (to_db_time(expires_at), lease_id)
    )
    return expires_at


def delete_lease(connection: sqlite3.Connection, lease_id: str) -> None:
    connection.execute("DELETE FROM leases WHERE lease_id = ?", (lease_id,))


# --- Task exclusions (do not offer a task again to a worker that failed it) ---


def add_task_exclusion(
    connection: sqlite3.Connection, job_id: str, worker_id: str, kind: TaskKind, now: datetime
) -> None:
    connection.execute(
        "INSERT INTO task_exclusions (job_id, worker_id, task_kind, created_at)"
        " VALUES (?, ?, ?, ?) ON CONFLICT DO NOTHING",
        (job_id, worker_id, str(kind), to_db_time(now)),
    )


def is_task_excluded(
    connection: sqlite3.Connection, job_id: str, worker_id: str, kind: TaskKind
) -> bool:
    row = connection.execute(
        "SELECT 1 FROM task_exclusions WHERE job_id = ? AND worker_id = ? AND task_kind = ?",
        (job_id, worker_id, str(kind)),
    ).fetchone()
    return row is not None


def clear_task_exclusions(connection: sqlite3.Connection, job_id: str) -> None:
    connection.execute("DELETE FROM task_exclusions WHERE job_id = ?", (job_id,))
