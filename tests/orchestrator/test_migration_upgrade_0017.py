"""Upgrading the database from schema 16 to 17 (LIVESTACK-51 item 3, duplicate videos).

Main (with LIVESTACK-48's 0016) leaves a database at version 16. 0017 adds
`jobs.duplicate_of` and two indexes.

The test builds a database as main leaves it, with two jobs that have the
same raw (a duplicate from before detection existed), opens it with this
code (as the orchestrator does at startup) and checks that 0017 runs and
that old jobs are left alone: no old job is called a duplicate after the
fact, since nobody was told and both may have been reviewed.
"""

import sqlite3

from orchestrator.db.database import Database, list_migrations

SCHEMA_ON_MAIN = 16
WHEN = "2026-11-07T12:00:00.000000Z"


def database_at_schema_16(data_dir):
    path = data_dir / "db" / "bvp.sqlite3"
    path.parent.mkdir(parents=True)
    connection = sqlite3.connect(path)
    for number, _, sql in list_migrations()[:SCHEMA_ON_MAIN]:
        connection.executescript(f"BEGIN;\n{sql}\nPRAGMA user_version = {number};\nCOMMIT;")
    # The same file sent twice before this change: two ordinary jobs.
    for job_id, message_id in (("job-1", "3EB0FIRST"), ("job-2", "3EB0AGAIN")):
        connection.execute(
            "INSERT INTO jobs (id, state, created_at, updated_at, message_id, sender_jid,"
            " sender_name, received_as, sha256, size_bytes, raw_extension, received_at)"
            " VALUES (?, 'awaiting_review', ?, ?, ?, '5519981234567@s.whatsapp.net',"
            " 'Maria', 'document', ?, 1000, '.mp4', ?)",
            (job_id, WHEN, WHEN, message_id, "b" * 64, WHEN),
        )
    connection.commit()
    connection.close()


def test_0017_runs_and_leaves_old_jobs_alone(tmp_path):
    database_at_schema_16(tmp_path)

    database = Database(tmp_path / "db" / "bvp.sqlite3")
    applied = database.apply_migrations()

    assert applied[0] == "0017_duplicate_videos.sql"
    with database.transaction() as connection:
        jobs = connection.execute("SELECT id, state, duplicate_of FROM jobs ORDER BY id").fetchall()
    assert [tuple(job) for job in jobs] == [
        ("job-1", "awaiting_review", None),
        ("job-2", "awaiting_review", None),
    ]
    database.close()
