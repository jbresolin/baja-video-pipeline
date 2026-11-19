"""Delete every video and everything about it from the jobs DB, keeping the setup.

    python clear_event_data.py /srv/pipeline/db/bvp.sqlite3

Run by clear_event_data.sh, inside a throwaway container from the
orchestrator's image (sqlite_in_orchestrator.sh), while the pipeline is
down. Standard library only, like sqlite_backup.py.

What goes (the test or last event's data):
  jobs, the PCs holding their raws, leases, clip versions, the clips each
  PC reported, task exclusions, the events of jobs, WhatsApp intake records,
  the senders' messages, the cached results tables (fetched again for the
  next event) and the PCs' speed samples.

What stays (the setup, so the next event starts ready):
  users and their sessions, contacts (with their default prova), provas,
  teams, Settings (including the pass-results config), enrolled workers and
  enrollment tokens, the record of one-time startup imports, and the admin
  change history (events not about a job).

Prints one line per table with the number of rows deleted. Exit status 0,
or 1 with the SQLite error (nothing is deleted then: one transaction).
"""

import sqlite3
import sys
from pathlib import Path

# Deleted in this order: rows that point at a job go before the jobs
# themselves (holders, leases and clip_versions have foreign keys to jobs).
# Each entry: (table, WHERE clause or "" for every row, why it goes).
TABLES_TO_EMPTY = [
    ("holders", "", "which PC holds which raw"),
    ("leases", "", "tasks in progress"),
    ("clip_versions", "", "the rendered versions of each clip"),
    ("worker_clips", "", "clips each PC reported having"),
    ("task_exclusions", "", "PCs that failed a task on a job"),
    ("events", "WHERE job_id IS NOT NULL", "the history of each job (admin history stays)"),
    ("intake_messages", "", "WhatsApp messages the bot took in"),
    ("sender_messages", "", "the senders' texts shown on the job page"),
    ("pass_result_tables", "", "cached official results, fetched again"),
    ("worker_stat_samples", "", "the PCs' speed measurements"),
    ("jobs", "", "the videos"),
]


def table_exists(connection: sqlite3.Connection, table: str) -> bool:
    """A DB from before a table was added simply has nothing to delete there."""
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
    ).fetchone()
    return row is not None


def clear_event_data(database_path: Path) -> list[tuple[str, int]]:
    """Empty the tables in TABLES_TO_EMPTY in one transaction; (table, rows deleted) each."""
    connection = sqlite3.connect(str(database_path))
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        deleted_rows = []
        with connection:  # one transaction: all of it or none of it
            for table, where_clause, _why in TABLES_TO_EMPTY:
                if not table_exists(connection, table):
                    continue
                cursor = connection.execute(f"DELETE FROM {table} {where_clause}")
                deleted_rows.append((table, cursor.rowcount))
        # Give the freed space back to the disk (the DB had every test video's rows).
        connection.execute("VACUUM")
        return deleted_rows
    finally:
        connection.close()


def main(arguments: list[str]) -> int:
    if len(arguments) != 1:
        print("usage: clear_event_data.py <path to bvp.sqlite3>", file=sys.stderr)
        return 2
    database_path = Path(arguments[0])
    if not database_path.is_file():
        print(f"clear_event_data: no database at {database_path}", file=sys.stderr)
        return 2
    try:
        deleted_rows = clear_event_data(database_path)
    except sqlite3.Error as error:
        print(f"clear_event_data: nothing deleted: {error}", file=sys.stderr)
        return 1
    for table, count in deleted_rows:
        print(f"clear_event_data: {table}: {count} rows deleted")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
