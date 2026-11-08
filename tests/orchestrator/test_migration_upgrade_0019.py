"""Upgrading the database from schema 18 to 19 (LIVESTACK-52, clip previews and downloads).

Main (with LIVESTACK-51's 0018) leaves a database at version 18. 0019 adds
five columns to `jobs`, all empty for existing jobs.

The test builds a database as main leaves it, with one delivered job, opens
it with this code (as the orchestrator does at startup) and checks that
0019 runs and that the old job is left as "never asked": no preview
wanted (its clip was rendered before previews existed) and no download.
"""

import sqlite3

from orchestrator.db.database import Database, list_migrations

SCHEMA_ON_MAIN = 18
WHEN = "2026-11-07T12:00:00.000000Z"
NEW_COLUMNS = (
    "preview_clip_sha256",
    "preview_wanted",
    "clip_download_requested_at",
    "clip_download_sha256",
    "clip_download_completed_at",
)


def database_at_schema_18(data_dir):
    path = data_dir / "db" / "bvp.sqlite3"
    path.parent.mkdir(parents=True)
    connection = sqlite3.connect(path)
    for number, _, sql in list_migrations()[:SCHEMA_ON_MAIN]:
        connection.executescript(f"BEGIN;\n{sql}\nPRAGMA user_version = {number};\nCOMMIT;")
    # A clip delivered before this change (the sender's number is invented).
    connection.execute(
        "INSERT INTO jobs (id, state, created_at, updated_at, message_id, sender_jid,"
        " sender_name, received_as, sha256, size_bytes, raw_extension, received_at)"
        " VALUES ('job-1', 'done', ?, ?, '3EB0OLD', '5500900000001@s.whatsapp.net',"
        " 'Fulana de Teste', 'document', ?, 1000, '.mp4', ?)",
        (WHEN, WHEN, "c" * 64, WHEN),
    )
    connection.commit()
    connection.close()


def test_0019_runs_and_leaves_old_jobs_without_previews_or_requests(tmp_path):
    database_at_schema_18(tmp_path)

    database = Database(tmp_path / "db" / "bvp.sqlite3")
    applied = database.apply_migrations()

    assert applied[0] == "0019_clip_review.sql"
    with database.transaction() as connection:
        row = connection.execute(
            f"SELECT {', '.join(NEW_COLUMNS)} FROM jobs WHERE id = 'job-1'"
        ).fetchone()
    # Every new column is empty: NULL preview_wanted reads as "rendered
    # before previews existed", so the page says "no preview" rather than
    # waiting for one that will never come.
    assert tuple(row) == (None,) * len(NEW_COLUMNS)
    database.close()
