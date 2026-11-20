"""Upgrading the database from schema 20 to 21 (LIVESTACK-67, the top speed under the clock).

What this module is for: main (with LIVESTACK-62's 0020) leaves a database
at version 20. 0021 adds one column to `jobs`, `speed_mark_s`: where the
reviewer marked the car crossing the speed trap.

What the test proves: 0021 runs on such a database, and a job reviewed
before it (with its largada marked) has no speed mark, so no clip changes
and nothing is flagged as out of date by the upgrade.

What it does not cover: what the mark does (test_avf_speed.py).
"""

import sqlite3

from orchestrator.db.database import Database, list_migrations

SCHEMA_ON_MAIN = 20
WHEN = "2026-11-20T12:00:00.000000Z"


def database_at_schema_20(data_dir):
    """A database as main leaves it, with one reviewed job (the sender's number is impossible)."""
    path = data_dir / "db" / "bvp.sqlite3"
    path.parent.mkdir(parents=True)
    connection = sqlite3.connect(path)
    for number, _, sql in list_migrations()[:SCHEMA_ON_MAIN]:
        connection.executescript(f"BEGIN;\n{sql}\nPRAGMA user_version = {number};\nCOMMIT;")
    connection.execute(
        "INSERT INTO jobs (id, state, created_at, updated_at, message_id, sender_jid,"
        " sender_name, received_as, sha256, size_bytes, raw_extension, received_at, prova,"
        " car_number, passagem, largada_s) VALUES ('job-1', 'done', ?, ?, '3EB0AVF',"
        " '5500900000042@s.whatsapp.net', 'Equipe de Filmagem Teste', 'document', ?, 1000,"
        " '.mp4', ?, 'Aceleração e Velocidade', 1, 1, 1.5)",
        (WHEN, WHEN, "e" * 64, WHEN),
    )
    connection.commit()
    connection.close()


def test_0021_runs_and_old_jobs_have_no_speed_mark(tmp_path):
    # Arrange: a database at schema 20 with a reviewed AVF job (largada marked).
    database_at_schema_20(tmp_path)

    # Act: open it with this code, as the orchestrator does at startup.
    database = Database(tmp_path / "db" / "bvp.sqlite3")
    applied = database.apply_migrations()

    # Assert: 0021 ran first, and the old job kept its largada and has no speed mark.
    later = [name for number, name, _ in list_migrations() if number > SCHEMA_ON_MAIN]
    assert applied == later, "0021 and every migration after it must run"
    assert applied[0] == "0021_speed_mark.sql", "0021 must run first"
    with database.transaction() as connection:
        row = connection.execute("SELECT largada_s, speed_mark_s FROM jobs").fetchone()
    assert row["largada_s"] == 1.5, "the upgrade must keep the largada"
    assert row["speed_mark_s"] is None, "an upgraded job must have no speed mark"
    database.close()
