"""Upgrading the production database from main's schema (LIVESTACK-38 migrations).

The VPS database was created and migrated by main's code: migrations
0001-0004 (the last two are LIVESTACK-37's admin page and results API).
LIVESTACK-38 added 0005-0007 (and LIVESTACK-42 0008, whose own upgrade test
is test_migration_0008.py). These tests build a database exactly as main
left it before LIVESTACK-38, put typical rows in it, then open it with this
code, which applies the newer migrations at startup, and check that:

* only 0005 to 0008 run, in order, and the version becomes 8;
* every existing row survives, and the new columns get the values the
  migrations promise (a job discarded before the upgrade counts as past
  its grace period, because main had already deleted its raws);
* the settings an admin saved stay, and the two new Settings values
  (discard grace, outbox wait) appear with their defaults.

A fresh database (no file yet) is covered by test_database.py; the last
test here checks it ends with the same columns as an upgraded one.
"""

import sqlite3

from orchestrator.db.database import Database, list_migrations, open_database
from orchestrator.testing.harness import build_harness

MAIN_SCHEMA_VERSION = 4
BRANCH_MIGRATIONS = [
    "0005_first_real_video.sql",
    "0006_worker_paused.sql",
    "0007_school_short.sql",
    "0008_clip_name_choices.sql",
]
WHEN = "2026-11-04T12:00:00.000000Z"


def database_as_main_leaves_it(data_dir):
    """A DB file with main's migrations only (0001-0004) and a few typical rows."""
    path = data_dir / "db" / "bvp.sqlite3"
    path.parent.mkdir(parents=True)
    connection = sqlite3.connect(path)
    for number, _, sql in list_migrations()[:MAIN_SCHEMA_VERSION]:
        connection.executescript(f"BEGIN;\n{sql}\nPRAGMA user_version = {number};\nCOMMIT;")
    job_columns = (
        "id, state, created_at, updated_at, message_id, received_as, sha256, size_bytes,"
        " raw_extension, finished_at"
    )
    connection.executemany(
        f"INSERT INTO jobs ({job_columns}) VALUES (?, ?, ?, ?, ?, 'document', ?, 10, '.mp4', ?)",
        [
            ("job-discarded", "discarded", WHEN, WHEN, "msg-1", "a" * 64, WHEN),
            ("job-waiting", "awaiting_review", WHEN, WHEN, "msg-2", "b" * 64, None),
        ],
    )
    connection.execute(
        "INSERT INTO teams (car_number, team_name, school, updated_at, source, short_name)"
        " VALUES (1, 'BAJA UFMG', 'UFMG', ?, 'results_api', 'Baja UFMG')",
        (WHEN,),
    )
    # An admin had lowered the inbox soft cap on main's Settings page.
    connection.execute(
        "INSERT INTO app_settings (key, value_json, updated_at) VALUES ('inbox_hold_gb', '15', ?)",
        (WHEN,),
    )
    connection.commit()
    connection.close()


def test_main_database_upgrades_with_only_the_new_migrations(tmp_path):
    database_as_main_leaves_it(tmp_path)

    database = open_database(tmp_path)

    # open_database applied 0005-0008 and nothing else.
    assert database.schema_version == len(list_migrations()) == 8
    with database.transaction() as connection:
        jobs = {
            row["id"]: row
            for row in connection.execute(
                "SELECT id, state, discarded_from, discard_grace_ends_at, media_info_json,"
                " overlay_school_short FROM jobs"
            )
        }
        team = connection.execute("SELECT * FROM teams WHERE car_number = 1").fetchone()
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master")}
    # Both jobs are still there, unchanged...
    assert jobs["job-waiting"]["state"] == "awaiting_review"
    assert jobs["job-discarded"]["state"] == "discarded"
    # ...the old discard is past its grace period (its raws are gone), so
    # "restore" will answer raw_unavailable unless a copy turns up...
    assert jobs["job-discarded"]["discard_grace_ends_at"] == WHEN
    assert jobs["job-discarded"]["discarded_from"] is None
    # ...and the jobs not discarded have no grace deadline.
    assert jobs["job-waiting"]["discard_grace_ends_at"] is None
    # The synced team keeps its values; the school short name waits for the
    # next sync.
    assert (team["team_name"], team["short_name"], team["school_short"]) == (
        "BAJA UFMG",
        "Baja UFMG",
        None,
    )
    # The clip archive tables exist and are empty.
    assert {"clip_versions", "worker_clips"} <= tables
    database.close()


def test_the_new_migrations_run_once(tmp_path):
    database_as_main_leaves_it(tmp_path)
    database = open_database(tmp_path)
    # A second start finds nothing new to apply.
    assert database.apply_migrations() == []
    database.close()


def test_the_upgrade_applies_exactly_this_branchs_files_in_order(tmp_path):
    database_as_main_leaves_it(tmp_path)

    database = Database(tmp_path / "db" / "bvp.sqlite3")
    assert database.schema_version == MAIN_SCHEMA_VERSION
    assert database.apply_migrations() == BRANCH_MIGRATIONS
    database.close()


def test_saved_settings_stay_and_the_two_new_ones_get_their_defaults(tmp_path):
    database_as_main_leaves_it(tmp_path)

    # The whole orchestrator starts on that data folder, as after a deploy.
    harness = build_harness(tmp_path)

    current = harness.context.admin_settings.current
    # The admin's 15 GB from main is kept (the table wins over .env)...
    assert current.inbox_hold_gb == 15
    # ...and the LIVESTACK-38 values start at 24 h and 72 h.
    assert current.discard_grace_hours == 24
    assert current.outbox_keep_unarchived_hours == 72
    harness.context.database.close()


def table_columns(database) -> dict[str, list[str]]:
    with database.transaction() as connection:
        names = [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
            )
        ]
        return {
            name: sorted(row[1] for row in connection.execute(f"PRAGMA table_info({name})"))
            for name in names
        }


def test_an_upgraded_database_has_the_same_tables_and_columns_as_a_fresh_one(tmp_path):
    database_as_main_leaves_it(tmp_path / "upgraded")
    upgraded = open_database(tmp_path / "upgraded")
    fresh = open_database(tmp_path / "fresh")

    assert table_columns(upgraded) == table_columns(fresh)
    upgraded.close()
    fresh.close()
