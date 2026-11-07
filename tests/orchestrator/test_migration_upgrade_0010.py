"""Upgrading the database from schema 9 to 10 (LIVESTACK-43, render inputs).

Main (with LIVESTACK-41's 0009) leaves a database at version 9. This
branch adds 0010: `clip_versions.render_inputs_json` (what each clip
version was rendered from) and two job columns that carry inputs until a
clip becomes a version (`claimed_render_inputs_json`,
`reported_render_inputs_json`).

The test builds a database as main leaves it, with a delivered job and its
clip version, opens it with this code and checks that only 0010 runs, the
rows survive, and the new columns start empty: nobody knows what clips
rendered before the upgrade were made from, so the review UI shows no
"out of date" warning for them rather than a wrong one.
"""

import sqlite3

from orchestrator.db.database import Database, list_migrations
from orchestrator.testing.harness import build_harness

SCHEMA_ON_MAIN = 9
WHEN = "2026-11-06T12:00:00.000000Z"


def database_at_schema_9(data_dir):
    path = data_dir / "db" / "bvp.sqlite3"
    path.parent.mkdir(parents=True)
    connection = sqlite3.connect(path)
    for number, _, sql in list_migrations()[:SCHEMA_ON_MAIN]:
        connection.executescript(f"BEGIN;\n{sql}\nPRAGMA user_version = {number};\nCOMMIT;")
    # A job whose clip was delivered before the upgrade: version 1 on the box.
    connection.execute(
        "INSERT INTO jobs (id, state, created_at, updated_at, message_id, received_as, sha256,"
        " size_bytes, raw_extension, clip_sha256, clip_size_bytes, clip_relative_path,"
        " clip_route, clip_rendered_by, clip_delivered_at) VALUES ('job-1', 'done', ?, ?,"
        " 'msg-1', 'document', ?, 10, '.mp4', ?, 5, 'enduro/42/enduro-042-p3.mp4', 'direct',"
        " 'w-1', ?)",
        (WHEN, WHEN, "a" * 64, "c" * 64, WHEN),
    )
    connection.execute(
        "INSERT INTO clip_versions (job_id, version, sha256, size_bytes, relative_path,"
        " rendered_at) VALUES ('job-1', 1, ?, 5, 'enduro/42/enduro-042-p3.mp4', ?)",
        ("c" * 64, WHEN),
    )
    connection.commit()
    connection.close()


def test_only_0010_runs_and_old_clips_have_no_recorded_inputs(tmp_path):
    database_at_schema_9(tmp_path)

    database = Database(tmp_path / "db" / "bvp.sqlite3")
    # 0010 runs first; later migrations (LIVESTACK-45's 0011) follow it.
    applied = database.apply_migrations()
    assert applied[0] == "0010_render_inputs.sql"
    assert applied == [name for number, name, _ in list_migrations() if number > SCHEMA_ON_MAIN]

    with database.transaction() as connection:
        job = connection.execute("SELECT * FROM jobs WHERE id = 'job-1'").fetchone()
        version = connection.execute("SELECT * FROM clip_versions").fetchone()
    # The job and its clip are untouched...
    assert (job["state"], job["clip_sha256"]) == ("done", "c" * 64)
    assert (version["version"], version["relative_path"]) == (1, "enduro/42/enduro-042-p3.mp4")
    # ...and nothing is known about what that clip was rendered from.
    assert version["render_inputs_json"] is None
    assert job["claimed_render_inputs_json"] is None
    assert job["reported_render_inputs_json"] is None
    database.close()


def test_an_upgraded_job_shows_no_comparison_rather_than_a_wrong_one(tmp_path):
    # The orchestrator opens the upgraded database (as it does at startup)
    # and a reviewer loads the job: without recorded inputs there is
    # nothing to compare, so Job.clip_inputs is null (no warning banner).
    database_at_schema_9(tmp_path / "data")
    harness = build_harness(tmp_path / "data")
    try:
        job = harness.client.get("/jobs/job-1", auth=harness.reviewer_auth).json()
        assert job["clip_versions"][0]["render_inputs"] is None
        assert job["clip_inputs"] is None
    finally:
        harness.client.close()
        harness.context.database.close()
