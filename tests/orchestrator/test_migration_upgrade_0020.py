"""Upgrading the database from schema 19 to 20 (LIVESTACK-62, wait for the official result).

What this module is for: main (with LIVESTACK-52's 0019) leaves a database
at version 19. 0020 adds one column to `jobs`, `result_hold_until`.

What the test proves: 0020 runs on such a database and a job that was
ready before it is not held (the column is empty), so an upgrade never
stops a render that was about to start.

What it does not cover: what a hold does (test_result_hold.py).
"""

import sqlite3

from orchestrator.db.database import Database, list_migrations

SCHEMA_ON_MAIN = 19
WHEN = "2026-11-14T12:00:00.000000Z"


def database_at_schema_19(data_dir):
    """A database as main leaves it, with one ready job (the sender's number is impossible)."""
    path = data_dir / "db" / "bvp.sqlite3"
    path.parent.mkdir(parents=True)
    connection = sqlite3.connect(path)
    for number, _, sql in list_migrations()[:SCHEMA_ON_MAIN]:
        connection.executescript(f"BEGIN;\n{sql}\nPRAGMA user_version = {number};\nCOMMIT;")
    connection.execute(
        "INSERT INTO jobs (id, state, created_at, updated_at, message_id, sender_jid,"
        " sender_name, received_as, sha256, size_bytes, raw_extension, received_at)"
        " VALUES ('job-1', 'ready', ?, ?, '3EB0READY', '5500900000042@s.whatsapp.net',"
        " 'Equipe de Filmagem Teste', 'document', ?, 1000, '.mp4', ?)",
        (WHEN, WHEN, "d" * 64, WHEN),
    )
    connection.commit()
    connection.close()


def test_0020_runs_and_leaves_ready_jobs_unheld(tmp_path):
    # Arrange: a database at schema 19 with a job that is ready to render.
    database_at_schema_19(tmp_path)

    # Act: open it with this code, as the orchestrator does at startup.
    database = Database(tmp_path / "db" / "bvp.sqlite3")
    applied = database.apply_migrations()

    # Assert: 0020 ran, and the ready job has no hold (it renders as before).
    assert applied == ["0020_result_hold.sql"], "exactly the new migration must run"
    with database.transaction() as connection:
        row = connection.execute("SELECT result_hold_until FROM jobs WHERE id = 'job-1'").fetchone()
    assert row["result_hold_until"] is None, "an upgraded job must not be held"
    database.close()
