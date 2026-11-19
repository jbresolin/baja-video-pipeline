"""Starting an event from scratch: infra/vps/clear_event_data.py and .sh.

What this is for: before an event João wants an empty pipeline (no test
videos in the queue, the Clipes tab or the VPS folders) without setting up
users, contacts, provas, teams, Settings and the PCs again. The Python part
deletes the job rows in one transaction; the shell part refuses while the
pipeline is up, asks for confirmation, keeps a safety copy of the DB and
empties the job folders.

What these tests prove:
  * on a real orchestrator DB with videos, the jobs and everything about
    them go, and the setup stays row for row; the orchestrator still opens
    the DB afterwards;
  * the admin history (events not about a job) stays;
  * the shell script refuses while the orchestrator is running, does
    nothing without "APAGAR", and with it keeps a safety copy, clears the
    DB and empties the job folders but not the DB folder.

Not covered: the real Docker run (sqlite_in_orchestrator.sh is covered in
test_backup.py) and the PCs' folders (a manual step, README §15).
"""

import importlib.util
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from orchestrator.db.database import open_database
from orchestrator.testing.harness import build_harness
from orchestrator.testing.scenarios import ensure_prova, receive_job
from shared.models import WorkerRole

VPS_FOLDER = Path(__file__).resolve().parents[2] / "infra" / "vps"
linux_only = pytest.mark.skipif(
    not sys.platform.startswith("linux") or shutil.which("bash") is None,
    reason="the shell script runs on the Linux VPS",
)

# Tables that hold the setup: they must come out of the clearing unchanged.
SETUP_TABLES = ["users", "contacts", "provas", "teams", "app_settings", "workers"]


def load_clear_event_data():
    """Import infra/vps/clear_event_data.py (it is a script, not a package)."""
    spec = importlib.util.spec_from_file_location(
        "clear_event_data", VPS_FOLDER / "clear_event_data.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def orchestrator_db_after_a_test_day(data_dir: Path) -> Path:
    """A real orchestrator DB with two test videos, a worker, a prova and a contact."""
    harness = build_harness(data_dir)
    harness.enroll(WorkerRole.MASTER, "desktop-test")
    ensure_prova(harness, "Manobrabilidade")
    receive_job(harness)
    receive_job(harness)
    harness.client.close()
    harness.context.database.close()
    return data_dir / "db" / "bvp.sqlite3"


def count_rows(database_path: Path, table: str, where_clause: str = "") -> int:
    """How many rows `table` has (optionally only those matching `where_clause`)."""
    connection = sqlite3.connect(database_path)
    try:
        return connection.execute(f"SELECT COUNT(*) FROM {table} {where_clause}").fetchone()[0]
    finally:
        connection.close()


def add_admin_history_line(database_path: Path) -> None:
    """An admin change, as the admin pages record it (an event with no job)."""
    connection = sqlite3.connect(database_path)
    with connection:
        connection.execute(
            "INSERT INTO events (at, actor, kind) VALUES (?, ?, ?)",
            ("2026-11-10T12:00:00+00:00", "admin", "settings_changed"),
        )
    connection.close()


# --- clear_event_data.py --------------------------------------------------------


def test_the_videos_go_and_the_setup_stays(tmp_path):
    # Arrange: a DB after a day of tests: two videos, the master enrolled,
    # the prova Manobrabilidade, the sender recorded as a contact.
    database_path = orchestrator_db_after_a_test_day(tmp_path / "data")
    setup_before = {table: count_rows(database_path, table) for table in SETUP_TABLES}
    assert count_rows(database_path, "jobs") == 2, "the test day left two videos"

    # Act: clear the event data.
    deleted_rows = load_clear_event_data().clear_event_data(database_path)

    # Assert: no job and nothing pointing at one is left...
    assert count_rows(database_path, "jobs") == 0, "every video must be gone"
    assert count_rows(database_path, "holders") == 0, "no PC holds a raw of a deleted job"
    assert count_rows(database_path, "intake_messages") == 0, "the bot's intake records go"
    assert count_rows(database_path, "events", "WHERE job_id IS NOT NULL") == 0, (
        "the jobs' history goes with them"
    )
    # ...the setup is exactly as it was...
    setup_after = {table: count_rows(database_path, table) for table in SETUP_TABLES}
    assert setup_after == setup_before, "users, contacts, provas, teams, Settings, workers stay"
    # ...and the counts say what was deleted.
    assert ("jobs", 2) in deleted_rows, "the report names the two videos deleted"


def test_the_admin_history_stays(tmp_path):
    # Arrange: a test day plus one admin change in the history.
    database_path = orchestrator_db_after_a_test_day(tmp_path / "data")
    add_admin_history_line(database_path)

    # Act: clear the event data.
    load_clear_event_data().clear_event_data(database_path)

    # Assert: the admin change is still there (it is not about a video).
    assert count_rows(database_path, "events", "WHERE kind = 'settings_changed'") == 1, (
        "the history of admin changes is kept"
    )


def test_the_orchestrator_still_opens_the_cleared_db(tmp_path):
    # Arrange: a cleared DB.
    data_dir = tmp_path / "data"
    database_path = orchestrator_db_after_a_test_day(data_dir)
    load_clear_event_data().clear_event_data(database_path)

    # Act: open it the way the orchestrator does at start (migrations included).
    database = open_database(data_dir)
    database.close()

    # Assert: nothing raised, and the setup is still there to work with.
    assert count_rows(database_path, "workers") == 1, "the enrolled PC is still enrolled"


def test_the_command_line_reports_each_table_and_refuses_a_missing_db(tmp_path, capsys):
    # Arrange: the script's command line, as sqlite_in_orchestrator.sh calls it.
    database_path = orchestrator_db_after_a_test_day(tmp_path / "data")
    script = load_clear_event_data()

    # Act: run it on the DB, then on a path with no DB.
    status_on_the_db = script.main([str(database_path)])
    report = capsys.readouterr().out
    status_without_a_db = script.main([str(tmp_path / "nowhere.sqlite3")])

    # Assert: it worked and said so per table; with no DB it stops with 2.
    assert status_on_the_db == 0, "clearing a real DB must succeed"
    assert "clear_event_data: jobs: 2 rows deleted" in report, "one line per table"
    assert status_without_a_db == 2, "a missing DB is a usage error, not a crash"


# --- clear_event_data.sh --------------------------------------------------------

# A stand-in for `docker`: `docker ps -q …` prints FAKE_ORCHESTRATOR_ID
# (empty = the pipeline is down). Any other call is a test failure.
FAKE_DOCKER = """#!/usr/bin/env bash
if [[ "$1" == "ps" ]]; then
    [[ -n "${FAKE_ORCHESTRATOR_ID:-}" ]] && echo "$FAKE_ORCHESTRATOR_ID"
    exit 0
fi
echo "fake docker: unexpected call $*" >&2
exit 99
"""


def prepare_vps(tmp_path: Path) -> tuple[dict, Path]:
    """A pretend VPS: the jobs DB and job files under a data folder, and a fake docker."""
    data_dir = tmp_path / "srv-pipeline"
    database_path = orchestrator_db_after_a_test_day(data_dir)
    for folder in ["inbox", "proxies", "thumbs", "outbox", "previews"]:
        (data_dir / folder).mkdir(exist_ok=True)
        (data_dir / folder / "4cb84d39-test.mp4").write_bytes(b"video bytes")
    tools = tmp_path / "bin"
    tools.mkdir()
    (tools / "docker").write_text(FAKE_DOCKER)
    (tools / "docker").chmod(0o755)
    environment = {
        **os.environ,
        "PATH": f"{tools}{os.pathsep}{os.environ['PATH']}",
        "BAJA_DATA_DIR": str(data_dir),
        "BAJA_BACKUP_ROOT": str(tmp_path / "backups"),
        "BAJA_CLEAR_PYTHON": sys.executable,
        "STACK_ENV_FILE": str(tmp_path / "no.env"),
    }
    return environment, database_path


def run_clear_script(environment: dict, typed_answer: str) -> subprocess.CompletedProcess:
    """Run clear_event_data.sh as João would, typing `typed_answer` at its question."""
    return subprocess.run(
        ["bash", str(VPS_FOLDER / "clear_event_data.sh")],
        input=typed_answer + "\n",
        env=environment,
        capture_output=True,
        text=True,
        timeout=120,
    )


@linux_only
def test_the_script_refuses_while_the_pipeline_is_up(tmp_path):
    # Arrange: the orchestrator container is running.
    environment, database_path = prepare_vps(tmp_path)
    environment["FAKE_ORCHESTRATOR_ID"] = "3f2a1b9c0d4e"

    # Act: run the script and type APAGAR anyway.
    completed = run_clear_script(environment, "APAGAR")

    # Assert: it stops, says to run down.sh first, and deletes nothing.
    assert completed.returncode == 2, completed.stdout + completed.stderr
    assert "down.sh" in completed.stderr, "the message says what to do first"
    assert count_rows(database_path, "jobs") == 2, "nothing may be deleted while it is up"


@linux_only
def test_the_script_does_nothing_without_apagar(tmp_path):
    # Arrange: the pipeline is down.
    environment, database_path = prepare_vps(tmp_path)

    # Act: answer something other than APAGAR.
    completed = run_clear_script(environment, "sim")

    # Assert: it stops and the videos and their files are all still there.
    assert completed.returncode == 2, completed.stdout + completed.stderr
    assert count_rows(database_path, "jobs") == 2, "a wrong answer deletes nothing"
    inbox_file = Path(environment["BAJA_DATA_DIR"]) / "inbox" / "4cb84d39-test.mp4"
    assert inbox_file.exists(), "the files stay too"


@linux_only
def test_the_script_keeps_a_copy_clears_the_db_and_empties_the_folders(tmp_path):
    # Arrange: the pipeline is down, with two videos and their files.
    environment, database_path = prepare_vps(tmp_path)
    data_dir = Path(environment["BAJA_DATA_DIR"])

    # Act: run it and type APAGAR.
    completed = run_clear_script(environment, "APAGAR")

    # Assert: it worked...
    assert completed.returncode == 0, completed.stdout + completed.stderr
    # ...a safety copy of the DB as it was is in the backups folder...
    [safety_folder] = list(Path(environment["BAJA_BACKUP_ROOT"]).glob("before-clear-*"))
    assert count_rows(safety_folder / "bvp.sqlite3", "jobs") == 2, (
        "the safety copy holds the DB from before the clearing"
    )
    # ...the DB has no videos, and the job folders are empty...
    assert count_rows(database_path, "jobs") == 0, "the videos are gone"
    for folder in ["inbox", "proxies", "thumbs", "outbox", "previews"]:
        assert list((data_dir / folder).iterdir()) == [], f"{folder}/ must be empty"
    # ...but the DB folder and the setup are untouched.
    assert database_path.exists(), "the DB itself stays"
    assert count_rows(database_path, "workers") == 1, "the enrolled PC stays enrolled"
    assert "up.sh" in completed.stdout, "it ends by saying how to start the pipeline again"
