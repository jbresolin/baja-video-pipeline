"""Upgrading the database from schema 14 to 15 (LIVESTACK-47, official pass results).

Main (with LIVESTACK-49's 0014) leaves a database at version 14. 0015 adds
the `pass_result_tables` table and `jobs.show_pass_result`.

The test builds a database as main leaves it, with one job, opens it with
this code (as the orchestrator does at startup) and checks that only 0015
runs, and that the old job follows its prova's default (the column is
NULL: never set by a reviewer).
"""

import sqlite3

from orchestrator.db.database import Database, list_migrations

SCHEMA_ON_MAIN = 14
WHEN = "2026-11-07T12:00:00.000000Z"


def database_at_schema_14(data_dir):
    path = data_dir / "db" / "bvp.sqlite3"
    path.parent.mkdir(parents=True)
    connection = sqlite3.connect(path)
    for number, _, sql in list_migrations()[:SCHEMA_ON_MAIN]:
        connection.executescript(f"BEGIN;\n{sql}\nPRAGMA user_version = {number};\nCOMMIT;")
    # A video reviewed before the upgrade.
    connection.execute(
        "INSERT INTO jobs (id, state, created_at, updated_at, message_id, sender_jid,"
        " sender_name, received_as, sha256, size_bytes, raw_extension, received_at, prova,"
        " car_number, passagem) VALUES ('job-1', 'done', ?, ?, '3EB0OLD',"
        " '5511987654321@s.whatsapp.net', 'Maria', 'document', ?, 1000, '.mp4', ?,"
        " 'Manobrabilidade', 26, 2)",
        (WHEN, WHEN, "a" * 64, WHEN),
    )
    connection.commit()
    connection.close()


def test_only_0015_runs_and_old_jobs_follow_the_provas_default(tmp_path):
    database_at_schema_14(tmp_path)

    database = Database(tmp_path / "db" / "bvp.sqlite3")
    applied = database.apply_migrations()

    # Exactly the new migration ran, and the job is untouched with no choice.
    assert applied == [name for number, name, _ in list_migrations() if number > SCHEMA_ON_MAIN]
    assert applied[0] == "0015_pass_results.sql"
    with database.transaction() as connection:
        job = connection.execute("SELECT * FROM jobs").fetchone()
        tables = connection.execute("SELECT COUNT(*) FROM pass_result_tables").fetchone()[0]
    assert (job["id"], job["passagem"], job["show_pass_result"]) == ("job-1", 2, None)
    # No results fetched yet.
    assert tables == 0
    database.close()
