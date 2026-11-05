"""The one-time imports when the orchestrator starts (orchestrator/domain/first_start.py).

The admin page replaces three files the operator used to edit. Nothing
already in them may be lost on the upgrade:

* reviewers.txt → users. Only into an EMPTY users table; the bcrypt hashes
  are reused, so everyone keeps their password. BVP_BOOTSTRAP_ADMIN names
  the one who becomes admin. Afterwards the file is ignored.
* BVP_TEAMS_FILE → teams. Once, into an empty table; afterwards ignored,
  even if the admin later deletes every team.
* Provas typed on existing jobs → the provas list. Once.

"Restarting" in these tests means closing the database and building a
new harness on the same data folder, which runs the startup code again.
"""

import logging

import pytest

from orchestrator.db.database import to_db_time
from orchestrator.domain.first_start import backfill_provas
from orchestrator.repositories import jobs as jobs_repo
from orchestrator.testing.harness import (
    SessionLogin,
    build_harness,
    write_reviewers_file,
)
from shared.testing.credentials import (
    ADMIN_USERNAME,
    PLACEHOLDER_ADMIN_PASSWORD,
    PLACEHOLDER_NEW_PASSWORD,
    REVIEWER_USERNAME,
    THIRD_USERNAME,
)


def restart(harness, tmp_path, **overrides):
    harness.context.database.close()
    return build_harness(tmp_path / "data", **overrides)


def users(harness) -> dict[str, str]:
    listed = harness.client.get("/admin/users", headers=harness.admin_headers).json()["users"]
    return {user["username"]: user["role"] for user in listed}


# --- reviewers.txt ---


def test_reviewers_are_imported_with_their_passwords_and_one_becomes_admin(harness):
    # The harness wrote reviewers.txt with ana and joao, BVP_BOOTSTRAP_ADMIN=joao.
    assert users(harness) == {"ana": "reviewer", "joao": "admin"}
    # Ana signs in with the password from the file: the hash was reused.
    assert harness.client.get("/auth/me", auth=harness.reviewer_auth).status_code == 200


def test_once_there_are_users_the_file_is_ignored_and_a_log_line_says_so(harness, tmp_path, caplog):
    # Someone edits the file after the first start: adds "carla" and gives
    # ana another password. (A separate file, because build_harness writes
    # its own reviewers.txt every time it starts.)
    edited = write_reviewers_file(
        tmp_path / "edited.txt",
        {
            REVIEWER_USERNAME: PLACEHOLDER_NEW_PASSWORD,
            ADMIN_USERNAME: PLACEHOLDER_NEW_PASSWORD,
            THIRD_USERNAME: PLACEHOLDER_NEW_PASSWORD,
        },
    )
    with caplog.at_level(logging.INFO, logger="orchestrator.domain.first_start"):
        restarted = restart(harness, tmp_path, reviewers_file=edited)
    # ...but users live in the database now: carla was not added, and ana's
    # password is still the one she had (not the file's new one).
    assert "carla" not in users(restarted)
    assert restarted.client.get("/auth/me", auth=restarted.reviewer_auth).status_code == 200
    assert "is ignored: users live in the database" in caplog.text


def test_a_bootstrap_admin_missing_from_the_file_is_warned_about(tmp_path, caplog):
    with caplog.at_level(logging.WARNING, logger="orchestrator.domain.first_start"):
        harness = build_harness(tmp_path / "data", bootstrap_admin="nobody")
    assert users(harness) == {"ana": "reviewer", "joao": "reviewer"}
    assert "BVP_BOOTSTRAP_ADMIN=nobody is not in" in caplog.text


def test_without_a_reviewers_file_the_admin_token_creates_the_first_admin(tmp_path):
    # A brand-new VPS with no reviewers.txt: no users at all...
    harness = build_harness(tmp_path / "data", reviewers_file=tmp_path / "missing.txt")
    assert users(harness) == {}
    # ...so the operator uses the admin token from .env once (README §4).
    created = harness.client.post(
        "/admin/users",
        json={"username": ADMIN_USERNAME, "password": PLACEHOLDER_ADMIN_PASSWORD, "role": "admin"},
        headers=harness.admin_headers,
    )
    assert created.status_code == 201
    joao = SessionLogin(ADMIN_USERNAME, PLACEHOLDER_ADMIN_PASSWORD)
    assert harness.client.get("/admin/users", auth=joao).status_code == 200


def test_a_reviewers_file_that_does_not_parse_stops_the_first_start(tmp_path):
    # Importing half a file would lock reviewers out; refuse to start instead.
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    bad = data_dir / "bad.txt"
    bad.write_text("ana:$2y$05$notahash\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="cannot import"):
        build_harness(data_dir, reviewers_file=bad)


# --- Teams file ---


def test_the_teams_file_is_imported_once_even_after_the_table_is_emptied(tmp_path):
    teams_file = tmp_path / "teams.csv"
    teams_file.write_text("42,Equipe Trovão,UFSC\n7,Mangue Baja,\n", encoding="utf-8")
    harness = build_harness(tmp_path / "data", teams_file=teams_file)
    listed = harness.client.get("/admin/teams", auth=harness.admin_auth).json()["teams"]
    assert [team["car_number"] for team in listed] == [7, 42]
    # The admin deletes every team, then the orchestrator restarts.
    for car in (7, 42):
        harness.client.delete(f"/admin/teams/{car}", auth=harness.admin_auth)
    restarted = restart(harness, tmp_path, teams_file=teams_file)
    # The file is not imported a second time.
    assert restarted.client.get("/admin/teams", auth=restarted.admin_auth).json()["teams"] == []


def test_a_teams_file_that_appears_later_is_still_imported_into_an_empty_table(tmp_path):
    # First start without the file (nothing to import, nothing recorded)...
    teams_file = tmp_path / "teams.csv"
    harness = build_harness(tmp_path / "data", teams_file=teams_file)
    # ...then the operator copies the file over and restarts.
    teams_file.write_text("5,Cinco,\n", encoding="utf-8")
    restarted = restart(harness, tmp_path, teams_file=teams_file)
    listed = restarted.client.get("/admin/teams", auth=restarted.admin_auth).json()["teams"]
    assert [team["team_name"] for team in listed] == ["Cinco"]


# --- Prova backfill ---


def insert_job_with_prova(harness, job_id: str, prova: str, minutes: int) -> None:
    """A job row as an older version of the orchestrator left it (free-text prova)."""
    created = to_db_time(harness.clock.now().replace(minute=minutes))
    with harness.context.database.transaction() as connection:
        jobs_repo.insert_job(
            connection,
            {
                "id": job_id,
                "state": "done",
                "created_at": created,
                "updated_at": created,
                "message_id": f"msg-{job_id}",
                "received_as": "document",
                "sha256": "0" * 64,
                "size_bytes": 1,
                "raw_extension": ".mp4",
                "prova": prova,
            },
        )


def test_every_prova_on_existing_jobs_becomes_a_prova_once(tmp_path):
    harness = build_harness(tmp_path / "data")
    insert_job_with_prova(harness, "j1", "Enduro", minutes=1)
    insert_job_with_prova(harness, "j2", "Suspensão", minutes=2)
    insert_job_with_prova(harness, "j3", "enduro", minutes=3)  # same folder as "Enduro"
    # The backfill is marked done on the first start (with no jobs), so
    # forget that and run it again, as an upgrade of an old database would.
    with harness.context.database.transaction() as connection:
        connection.execute("DELETE FROM first_start_imports WHERE name = 'prova_backfill'")
    assert backfill_provas(harness.context.database, harness.clock.now()) == 2

    listed = harness.client.get("/provas", auth=harness.reviewer_auth).json()["provas"]
    # Oldest first, as the jobs used them; "enduro" merged into "Enduro"...
    assert [(p["name"], p["slug"]) for p in listed] == [
        ("Enduro", "enduro"),
        ("Suspensão", "suspensao"),
    ]
    # ...and its job relabelled, so every job has a prova from the list.
    assert harness.job("j3")["prova"] == "Enduro"
    # A second run does nothing.
    assert backfill_provas(harness.context.database, harness.clock.now()) == 0
