"""Upgrading the database from schema 12 to 13 (LIVESTACK-50, render options).

Main leaves a database at version 12. 0013 adds the picture size players
show (display_width/height) and the reviewer's render options (rotation,
framing, crop_position, no_audio) to jobs.

The test builds a database as main leaves it, with one job of an upright
phone video already probed (its report stored), opens it with this code
as the orchestrator does at startup, and checks that: only 0013 runs; the
job gets the defaults every job had before (no turn, blurred sides, band
in the middle, sound kept); and the first start reads its size from the
stored report, so the queue marks it vertical, without any bot reply.
"""

import json
import sqlite3

from orchestrator.db.database import Database, list_migrations
from orchestrator.testing.harness import build_harness
from tests.orchestrator.test_render_options import PORTRAIT_PHONE_REPORT

SCHEMA_ON_MAIN = 12
WHEN = "2026-11-06T12:00:00.000000Z"
JOB_ID = "job-from-before"


def database_at_schema_12(data_dir):
    path = data_dir / "db" / "bvp.sqlite3"
    path.parent.mkdir(parents=True)
    connection = sqlite3.connect(path)
    for number, _, sql in list_migrations()[:SCHEMA_ON_MAIN]:
        connection.executescript(f"BEGIN;\n{sql}\nPRAGMA user_version = {number};\nCOMMIT;")
    # A video reviewed before this upgrade, with its probe report stored.
    connection.execute(
        "INSERT INTO jobs (id, state, created_at, updated_at, message_id, sender_jid,"
        " received_as, sha256, size_bytes, raw_extension, media_info_json,"
        " received_at, recorded_at, recorded_at_source)"
        " VALUES (?, 'awaiting_review', ?, ?, 'msg-1', '5511987654321@s.whatsapp.net',"
        " 'document', ?, 1000, '.mp4', ?, ?, ?, 'received')",
        (JOB_ID, WHEN, WHEN, "a" * 64, json.dumps(PORTRAIT_PHONE_REPORT), WHEN, WHEN),
    )
    connection.commit()
    connection.close()


def test_only_0013_runs_and_old_jobs_get_the_old_behaviour_as_options(tmp_path):
    database_at_schema_12(tmp_path)

    database = Database(tmp_path / "db" / "bvp.sqlite3")
    applied = database.apply_migrations()
    assert applied == [name for number, name, _ in list_migrations() if number > SCHEMA_ON_MAIN]
    assert applied[0] == "0013_render_options.sql"

    with database.transaction() as connection:
        job = connection.execute("SELECT * FROM jobs").fetchone()
    assert (job["rotation"], job["framing"], job["crop_position"], job["no_audio"]) == (
        0,
        "blurred",
        0.5,
        0,
    )
    # The size is not known yet: the migration does not parse JSON.
    assert job["display_width"] is None
    database.close()


def test_the_first_start_reads_the_size_of_older_jobs_without_a_bot_reply(tmp_path):
    database_at_schema_12(tmp_path / "data")
    harness = build_harness(tmp_path / "data")
    try:
        job = harness.job(JOB_ID)
        assert (job["display_width"], job["display_height"], job["vertical"]) == (1080, 1920, True)
        # Asking a filmmaker about a video from days ago would only confuse.
        assert harness.evolution.sent == []
    finally:
        harness.client.close()
        harness.context.database.close()
