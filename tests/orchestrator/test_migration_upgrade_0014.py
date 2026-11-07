"""Upgrading the database from schema 13 to 14 (LIVESTACK-49, the sender's messages).

Main (with LIVESTACK-50's 0013) leaves a database at version 13. 0014 adds
the `sender_messages` table, `jobs.caption` and an index of jobs by sender.

The test builds a database as main leaves it, with one job, opens it with
this code (as the orchestrator does at startup) and checks that only 0014
runs, the job survives without a caption (none was kept before), and its
page can list a message the sender writes after the upgrade.
"""

import sqlite3
from datetime import UTC, datetime

from orchestrator.db.database import Database, list_migrations
from orchestrator.testing.harness import build_harness
from orchestrator.testing.scenarios import post_webhook, sender_messages
from shared.testing.evolution_payloads import text_message_payload

SCHEMA_ON_MAIN = 13
WHEN = "2026-11-06T12:00:00.000000Z"
PHONE = "5511987654321"
SHA = "a" * 64


def database_at_schema_13(data_dir):
    path = data_dir / "db" / "bvp.sqlite3"
    path.parent.mkdir(parents=True)
    connection = sqlite3.connect(path)
    for number, _, sql in list_migrations()[:SCHEMA_ON_MAIN]:
        connection.executescript(f"BEGIN;\n{sql}\nPRAGMA user_version = {number};\nCOMMIT;")
    # A video Maria sent before the upgrade.
    connection.execute(
        "INSERT INTO jobs (id, state, created_at, updated_at, message_id, sender_jid,"
        " sender_name, received_as, sha256, size_bytes, raw_extension, received_at)"
        " VALUES ('job-1', 'awaiting_review', ?, ?, '3EB0OLD', ?, 'Maria', 'document',"
        " ?, 1000, '.mp4', ?)",
        (WHEN, WHEN, f"{PHONE}@s.whatsapp.net", SHA, WHEN),
    )
    connection.commit()
    connection.close()


def test_only_0014_runs_and_old_jobs_have_no_caption(tmp_path):
    database_at_schema_13(tmp_path)

    database = Database(tmp_path / "db" / "bvp.sqlite3")
    applied = database.apply_migrations()
    assert applied == [name for number, name, _ in list_migrations() if number > SCHEMA_ON_MAIN]
    assert applied[0] == "0014_sender_messages.sql"

    with database.transaction() as connection:
        job = connection.execute("SELECT * FROM jobs").fetchone()
        messages = connection.execute("SELECT COUNT(*) FROM sender_messages").fetchone()[0]
    assert (job["id"], job["sender_name"], job["caption"]) == ("job-1", "Maria", None)
    assert messages == 0
    database.close()


def test_an_upgraded_jobs_page_lists_what_the_sender_writes_next(tmp_path):
    # The orchestrator opens the upgraded database; a day after the video
    # arrived, Maria writes about it.
    database_at_schema_13(tmp_path / "data")
    harness = build_harness(tmp_path / "data", clock_start=datetime(2026, 11, 7, 12, tzinfo=UTC))
    try:
        post_webhook(harness, text_message_payload("era o carro 42", PHONE))
        items = sender_messages(harness, "job-1")
        assert [(item["role"], item["text"]) for item in items] == [("after", "era o carro 42")]
    finally:
        harness.client.close()
        harness.context.database.close()
