"""Upgrading the database from schema 15 to 16 (LIVESTACK-48, the running clock).

Main (with LIVESTACK-47's 0015) leaves a database at version 15. 0016 adds
`jobs.largada_s` and `jobs.show_clock`.

The test builds a database as main leaves it, with one job, opens it with
this code (as the orchestrator does at startup) and checks that 0016 and
the later migrations run, and that the old job has no largada (so no clock) and a toggle never
set (NULL: the prova's default applies).
"""

import sqlite3

from orchestrator.db.database import Database, list_migrations

SCHEMA_ON_MAIN = 15
WHEN = "2026-11-07T12:00:00.000000Z"


def database_at_schema_15(data_dir):
    path = data_dir / "db" / "bvp.sqlite3"
    path.parent.mkdir(parents=True)
    connection = sqlite3.connect(path)
    for number, _, sql in list_migrations()[:SCHEMA_ON_MAIN]:
        connection.executescript(f"BEGIN;\n{sql}\nPRAGMA user_version = {number};\nCOMMIT;")
    # A video reviewed before the upgrade, its result toggle set.
    connection.execute(
        "INSERT INTO jobs (id, state, created_at, updated_at, message_id, sender_jid,"
        " sender_name, received_as, sha256, size_bytes, raw_extension, received_at, prova,"
        " car_number, passagem, show_pass_result) VALUES ('job-1', 'done', ?, ?, '3EB0OLD',"
        " '5511987654321@s.whatsapp.net', 'Maria', 'document', ?, 1000, '.mp4', ?,"
        " 'Manobrabilidade', 26, 2, 1)",
        (WHEN, WHEN, "a" * 64, WHEN),
    )
    connection.commit()
    connection.close()


def test_0016_runs_and_old_jobs_have_no_clock(tmp_path):
    database_at_schema_15(tmp_path)

    database = Database(tmp_path / "db" / "bvp.sqlite3")
    applied = database.apply_migrations()

    assert applied == [name for number, name, _ in list_migrations() if number > SCHEMA_ON_MAIN]
    assert applied[0] == "0016_pass_clock.sql"
    with database.transaction() as connection:
        job = connection.execute("SELECT * FROM jobs").fetchone()
    # Untouched, with no largada and the clock toggle never set.
    assert (job["id"], job["show_pass_result"]) == ("job-1", 1)
    assert (job["largada_s"], job["show_clock"]) == (None, None)
    database.close()
