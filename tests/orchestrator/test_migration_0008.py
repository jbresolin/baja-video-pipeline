"""Upgrading today's production database (schema 7) to LIVESTACK-42's schema 8.

Background for a beginner: the orchestrator's database changes shape
through numbered SQL files ("migrations"); at startup it applies the ones
the database has not seen yet. The VPS runs main's code, whose database is
at version 7. LIVESTACK-42 adds 0008, which gives every job a `school_name`
column (the school's full name, for the "Nome da escola nos vídeos:
completo" choice on the Settings page).

These tests build a version-7 database with a reviewed job in it, open it
with this code and check that only 0008 runs, the job survives, and a job
reviewed before the upgrade (no full school name stored) still gets a
school on its clip: the sigla, the fallback.
"""

import sqlite3

from orchestrator.db.database import Database, list_migrations
from orchestrator.domain.clip_names import NameStyles, clip_names_for_job
from shared.models import NameStyle

MAIN_SCHEMA_VERSION = 7
WHEN = "2026-11-05T12:00:00.000000Z"


def database_as_main_leaves_it_today(data_dir):
    """A DB file with migrations 0001-0007 and one job reviewed for car 1."""
    path = data_dir / "bvp.sqlite3"
    connection = sqlite3.connect(path)
    for number, _, sql in list_migrations()[:MAIN_SCHEMA_VERSION]:
        connection.executescript(f"BEGIN;\n{sql}\nPRAGMA user_version = {number};\nCOMMIT;")
    connection.execute(
        "INSERT INTO jobs (id, state, created_at, updated_at, message_id, received_as, sha256,"
        " size_bytes, raw_extension, prova, car_number, team_name, overlay_team_name,"
        " overlay_school_short)"
        " VALUES ('job-1', 'ready', ?, ?, 'msg-1', 'document', ?, 10, '.mp4', 'Enduro', 1,"
        " 'BAJA UFMG', 'Baja UFMG', 'UFMG')",
        (WHEN, WHEN, "a" * 64),
    )
    connection.commit()
    connection.close()
    return path


def test_only_0008_runs_and_the_job_survives_without_a_full_school_name(tmp_path):
    path = database_as_main_leaves_it_today(tmp_path)

    database = Database(path)
    assert database.schema_version == MAIN_SCHEMA_VERSION
    assert database.apply_migrations() == ["0008_clip_name_choices.sql"]

    with database.transaction() as connection:
        job = connection.execute("SELECT * FROM jobs WHERE id = 'job-1'").fetchone()
    # The job is untouched; it simply has no full school name yet.
    assert (job["state"], job["team_name"], job["overlay_school_short"]) == (
        "ready",
        "BAJA UFMG",
        "UFMG",
    )
    assert job["school_name"] is None
    # Even with "completo" chosen for the school, its clip shows the sigla
    # rather than nothing, and the team follows its own choice.
    names = clip_names_for_job(NameStyles(team=NameStyle.FULL, school=NameStyle.FULL), job)
    assert (names.team, names.school) == ("BAJA UFMG", "UFMG")
    database.close()
