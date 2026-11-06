"""Upgrading the database from schema 8 to 9 (LIVESTACK-41).

Main (with LIVESTACK-42's 0008) leaves a database at version 8. This
branch adds 0009:
`workers.shared_tailnet` (item 4), `jobs.raw_file_name` (item 12) and
`jobs.original_requested_at` (item 15). These tests build a database as
main leaves it, with typical rows, open it with this code, and check that
only 0009 runs, every row survives, and the new columns start empty or
false: old workers are on the Baja tailnet, old jobs keep "<job_id>.<ext>"
raw names on the workers, and nobody has asked for an original yet.
(The VPS itself may still be at 7: then 0008 and 0009 both run, which
test_migration_0008.py covers.)
"""

import sqlite3

from orchestrator.db.database import Database, list_migrations, open_database

SCHEMA_ON_MAIN = 8
WHEN = "2026-11-05T12:00:00.000000Z"


def database_at_schema_8(data_dir):
    path = data_dir / "db" / "bvp.sqlite3"
    path.parent.mkdir(parents=True)
    connection = sqlite3.connect(path)
    for number, _, sql in list_migrations()[:SCHEMA_ON_MAIN]:
        connection.executescript(f"BEGIN;\n{sql}\nPRAGMA user_version = {number};\nCOMMIT;")
    connection.execute(
        "INSERT INTO jobs (id, state, created_at, updated_at, message_id, received_as, sha256,"
        " size_bytes, raw_extension) VALUES ('job-1', 'done', ?, ?, 'msg-1', 'document', ?, 10,"
        " '.mp4')",
        (WHEN, WHEN, "a" * 64),
    )
    connection.execute(
        "INSERT INTO workers (id, name, role, capabilities_json, bearer_token_sha256,"
        " storage_cap_bytes, enrolled_at, paused) VALUES ('w-1', 'desktop', 'master',"
        ' \'{"has_nvenc": true, "video_encoder": "h264_nvenc"}\', ?, 1, ?, 1)',
        ("b" * 64, WHEN),
    )
    connection.commit()
    connection.close()


def test_only_0009_runs_and_the_rows_survive_with_empty_new_columns(tmp_path):
    database_at_schema_8(tmp_path)

    database = Database(tmp_path / "db" / "bvp.sqlite3")
    assert database.apply_migrations() == ["0009_worker_real_use.sql"]

    with database.transaction() as connection:
        job = connection.execute("SELECT * FROM jobs WHERE id = 'job-1'").fetchone()
        worker = connection.execute("SELECT * FROM workers WHERE id = 'w-1'").fetchone()
    assert job["state"] == "done"
    assert job["raw_file_name"] is None
    assert job["original_requested_at"] is None
    # The worker keeps what it had (paused) and is not on a shared tailnet
    # until a heartbeat says so.
    assert worker["paused"] == 1 and worker["shared_tailnet"] == 0
    database.close()


def test_a_second_start_applies_nothing(tmp_path):
    database_at_schema_8(tmp_path)
    open_database(tmp_path).close()
    database = open_database(tmp_path)
    assert database.apply_migrations() == []
    assert database.schema_version == len(list_migrations()) == 9
    database.close()
