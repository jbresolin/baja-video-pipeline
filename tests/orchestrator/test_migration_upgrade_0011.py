"""Upgrading the database from schema 10 to 11 (LIVESTACK-45, recording times).

Main (with LIVESTACK-43's 0010) leaves a database at version 10, whose
jobs know when they arrived but not when they were filmed. 0011 adds
`sent_at`, `received_at`, `recorded_at` and `recorded_at_source`, and
gives every existing job its creation time as recording time ("received").
At the first start after the upgrade, the orchestrator then reads the
creation_time of every probe report already stored (domain/first_start.py),
once, so jobs reviewed during an event keep a useful order.

The test builds a database as main leaves it, with three jobs, opens it
with this code (as the orchestrator does at startup) and checks each job.
"""

import json
import sqlite3

from orchestrator.db.database import Database, list_migrations
from orchestrator.domain.first_start import backfill_recorded_at
from orchestrator.testing.harness import build_harness

SCHEMA_ON_MAIN = 10
# The jobs arrived at 14:40, 14:41 and 14:42 on 2026-03-26 (UTC-3).
ARRIVED = {
    "with-good-probe": "2026-03-26T17:40:00.000000Z",
    "without-probe": "2026-03-26T17:41:00.000000Z",
    "with-bad-probe": "2026-03-26T17:42:00.000000Z",
}


def probe_json(creation_time: str) -> str:
    return json.dumps(
        {
            "format": {"tags": {"creation_time": creation_time}},
            "streams": [{"codec_type": "video"}],
            "location_present": False,
        }
    )


def database_at_schema_10(data_dir):
    path = data_dir / "db" / "bvp.sqlite3"
    path.parent.mkdir(parents=True)
    connection = sqlite3.connect(path)
    for number, _, sql in list_migrations()[:SCHEMA_ON_MAIN]:
        connection.executescript(f"BEGIN;\n{sql}\nPRAGMA user_version = {number};\nCOMMIT;")
    probes = {
        # Filmed at 14:02: believable.
        "with-good-probe": probe_json("2026-03-26T17:02:00.000000Z"),
        # No probe report yet (no holder had made the proxy).
        "without-probe": None,
        # A camera that thinks it is 1970: not believed.
        "with-bad-probe": probe_json("1970-01-01T00:00:00.000000Z"),
    }
    for job_id, arrived in ARRIVED.items():
        connection.execute(
            "INSERT INTO jobs (id, state, created_at, updated_at, message_id, received_as,"
            " sha256, size_bytes, raw_extension, media_info_json)"
            " VALUES (?, 'awaiting_review', ?, ?, ?, 'document', ?, 10, '.mp4', ?)",
            (job_id, arrived, arrived, f"msg-{job_id}", "a" * 64, probes[job_id]),
        )
    connection.commit()
    connection.close()


def recorded(connection, job_id):
    row = connection.execute(
        "SELECT recorded_at, recorded_at_source, received_at, sent_at, updated_at"
        " FROM jobs WHERE id = ?",
        (job_id,),
    ).fetchone()
    return dict(row)


def test_0011_gives_every_old_job_its_arrival_as_recording_time(tmp_path):
    database_at_schema_10(tmp_path)

    database = Database(tmp_path / "db" / "bvp.sqlite3")
    # 0011 runs first; later migrations (LIVESTACK-44's 0012) follow it.
    applied = database.apply_migrations()
    assert applied[0] == "0011_recorded_at.sql"
    assert applied == [name for number, name, _ in list_migrations() if number > SCHEMA_ON_MAIN]

    with database.transaction() as connection:
        for job_id, arrived in ARRIVED.items():
            # Before the startup step, every job sorts by arrival, marked as such.
            assert recorded(connection, job_id) == {
                "recorded_at": arrived,
                "recorded_at_source": "received",
                "received_at": arrived,
                # WhatsApp's timestamp was never stored before: unknown.
                "sent_at": None,
                "updated_at": arrived,
            }
    database.close()


def test_the_first_start_reads_stored_probe_reports_once(tmp_path):
    database_at_schema_10(tmp_path / "data")

    # Starting the orchestrator applies 0011 and runs the startup step.
    harness = build_harness(tmp_path / "data")
    database = harness.context.database
    try:
        with database.transaction() as connection:
            good = recorded(connection, "with-good-probe")
            missing = recorded(connection, "without-probe")
            bad = recorded(connection, "with-bad-probe")
        # The stored probe said 14:02 and that is plausible: exact now.
        assert (good["recorded_at"], good["recorded_at_source"]) == (
            "2026-03-26T17:02:00.000000Z",
            "metadata",
        )
        # Nobody edited the job: updated_at stays as it was.
        assert good["updated_at"] == ARRIVED["with-good-probe"]
        # No probe, or an unbelievable one: the arrival time stays.
        assert (missing["recorded_at"], missing["recorded_at_source"]) == (
            ARRIVED["without-probe"],
            "received",
        )
        assert (bad["recorded_at"], bad["recorded_at_source"]) == (
            ARRIVED["with-bad-probe"],
            "received",
        )
        # The step is recorded as done; running it again changes nothing.
        assert backfill_recorded_at(database, harness.clock.now()) == 0
    finally:
        harness.client.close()
        database.close()
