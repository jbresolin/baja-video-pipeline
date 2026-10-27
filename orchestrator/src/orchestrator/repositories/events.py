"""SQL for the append-only events log (who did what, when and why)."""

import sqlite3
from dataclasses import dataclass
from datetime import datetime

from orchestrator.db.database import from_db_time, to_db_time

SYSTEM_ACTOR = "system"


def worker_actor(worker_id: str) -> str:
    return f"worker:{worker_id}"


def reviewer_actor(username: str) -> str:
    return f"reviewer:{username}"


@dataclass(frozen=True)
class Event:
    at: datetime
    job_id: str | None
    worker_id: str | None
    actor: str
    kind: str
    from_state: str | None
    to_state: str | None
    reason: str | None


def record_event(
    connection: sqlite3.Connection,
    now: datetime,
    *,
    actor: str,
    kind: str,
    job_id: str | None = None,
    worker_id: str | None = None,
    from_state: str | None = None,
    to_state: str | None = None,
    reason: str | None = None,
) -> None:
    connection.execute(
        "INSERT INTO events (at, job_id, worker_id, actor, kind, from_state, to_state, reason)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (to_db_time(now), job_id, worker_id, actor, kind, from_state, to_state, reason),
    )


def list_events_for_job(connection: sqlite3.Connection, job_id: str) -> list[Event]:
    rows = connection.execute(
        "SELECT * FROM events WHERE job_id = ? ORDER BY id", (job_id,)
    ).fetchall()
    return [_event_from_row(row) for row in rows]


def list_events_for_worker(connection: sqlite3.Connection, worker_id: str) -> list[Event]:
    rows = connection.execute(
        "SELECT * FROM events WHERE worker_id = ? ORDER BY id", (worker_id,)
    ).fetchall()
    return [_event_from_row(row) for row in rows]


def _event_from_row(row: sqlite3.Row) -> Event:
    return Event(
        at=from_db_time(row["at"]),
        job_id=row["job_id"],
        worker_id=row["worker_id"],
        actor=row["actor"],
        kind=row["kind"],
        from_state=row["from_state"],
        to_state=row["to_state"],
        reason=row["reason"],
    )
