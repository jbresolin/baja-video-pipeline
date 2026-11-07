"""SQL for the pass_result_tables table (LIVESTACK-47) and the jobs that need fresh results.

What to fetch and when is domain/pass_results_fetch.py's job; reading a
table into texts is domain/pass_results.py's.
"""

import sqlite3
from datetime import datetime

from orchestrator.db.database import to_db_time
from shared.models import JobState

# Jobs whose results matter now: in review, waiting for or in their render,
# or with a clip uploaded and not yet confirmed.
ACTIVE_STATES = (
    JobState.AWAITING_REVIEW,
    JobState.READY,
    JobState.CLAIMED,
    JobState.UPLOADED,
)
# Delivered clips matter for a while too (a corrected result flags them).
RECENTLY_DELIVERED_STATES = (JobState.DELIVERED, JobState.DONE)


def get_table_row(connection: sqlite3.Connection, resultado_id: str) -> sqlite3.Row | None:
    return connection.execute(
        "SELECT * FROM pass_result_tables WHERE resultado_id = ?", (resultado_id,)
    ).fetchone()


def record_success(
    connection: sqlite3.Connection,
    resultado_id: str,
    body_json: str,
    max_age_s: int | None,
    now: datetime,
) -> None:
    at = to_db_time(now)
    connection.execute(
        "INSERT INTO pass_result_tables (resultado_id, body_json, success_at, attempt_at,"
        " attempt_ok, error, failing_since, max_age_s) VALUES (?, ?, ?, ?, 1, NULL, NULL, ?)"
        " ON CONFLICT (resultado_id) DO UPDATE SET body_json = excluded.body_json,"
        " success_at = excluded.success_at, attempt_at = excluded.attempt_at, attempt_ok = 1,"
        " error = NULL, failing_since = NULL, max_age_s = excluded.max_age_s",
        (resultado_id, body_json, at, at, max_age_s),
    )


def record_failure(
    connection: sqlite3.Connection, resultado_id: str, error: str, now: datetime
) -> None:
    """The last good answer stays; `failing_since` keeps the first failure in a row."""
    at = to_db_time(now)
    connection.execute(
        "INSERT INTO pass_result_tables (resultado_id, attempt_at, attempt_ok, error,"
        " failing_since) VALUES (?, ?, 0, ?, ?)"
        " ON CONFLICT (resultado_id) DO UPDATE SET attempt_at = excluded.attempt_at,"
        " attempt_ok = 0, error = excluded.error,"
        " failing_since = COALESCE(pass_result_tables.failing_since, excluded.failing_since)",
        (resultado_id, at, error, at),
    )


def prova_names_with_jobs_needing_results(
    connection: sqlite3.Connection, delivered_since: datetime
) -> set[str]:
    """Prova names (as stored on jobs) of jobs in ACTIVE_STATES, or delivered since then."""
    active = ", ".join("?" for _ in ACTIVE_STATES)
    delivered = ", ".join("?" for _ in RECENTLY_DELIVERED_STATES)
    rows = connection.execute(
        f"SELECT DISTINCT prova FROM jobs WHERE prova IS NOT NULL AND (state IN ({active})"
        f" OR (state IN ({delivered}) AND updated_at >= ?))",
        (
            *(str(state) for state in ACTIVE_STATES),
            *(str(state) for state in RECENTLY_DELIVERED_STATES),
            to_db_time(delivered_since),
        ),
    ).fetchall()
    return {row["prova"] for row in rows}
