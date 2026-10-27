"""Tests for the VPS backups: infra/vps/sqlite_backup.py and infra/vps/backup.sh.

The jobs DB (db/bvp.sqlite3) is the one piece of pipeline state that exists
only on the VPS, so the most important test here is the round trip: back
up a live orchestrator's DB, restore it into an empty data folder, start an
orchestrator on it, and find the same jobs (and nothing written after the
backup). The shell script is then run with fake `docker` and `setpriv`
programs to check the folder it writes, the checksums and the retention.
"""

import hashlib
import importlib.util
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from orchestrator.testing.harness import build_harness
from orchestrator.testing.scenarios import receive_job

VPS_FOLDER = Path(__file__).resolve().parents[2] / "infra" / "vps"


def load_sqlite_backup():
    """sqlite_backup.py is a script, not a package: load it from its file."""
    spec = importlib.util.spec_from_file_location("sqlite_backup", VPS_FOLDER / "sqlite_backup.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


sqlite_backup = load_sqlite_backup()


def close(harness) -> None:
    harness.client.close()
    harness.context.database.close()


def live_db_path(harness) -> Path:
    return harness.context.settings.data_dir / "db" / "bvp.sqlite3"


# --- SQLite round trip ----------------------------------------------------------


def test_backup_of_a_running_orchestrator_restores_into_a_fresh_one(tmp_path):
    # Set up: an orchestrator that is running (its DB is open in WAL mode,
    # so recent writes may still sit in the -wal file) with two jobs.
    original = build_harness(tmp_path / "vps")
    first_job = receive_job(original, sender_phone="5511900000001")
    second_job = receive_job(original, sender_phone="5511900000002")
    assert live_db_path(original).with_name("bvp.sqlite3-wal").exists()

    # Act 1: back up while it runs, then let it write a third job AFTER the backup.
    backup_file = tmp_path / "backups" / "bvp.sqlite3"
    backup_file.parent.mkdir()
    jobs_in_backup = sqlite_backup.backup(live_db_path(original), backup_file)
    late_job = receive_job(original, sender_phone="5511900000003")
    close(original)

    # The copy holds exactly the two jobs that existed at backup time, and
    # it is one self-contained file (no -wal needed beside it).
    assert jobs_in_backup == 2
    assert not backup_file.with_name("bvp.sqlite3-wal").exists()

    # Act 2: restore it into a brand-new, empty data folder (a rebuilt VPS)
    # and start an orchestrator on it.
    new_db = tmp_path / "new-vps" / "db" / "bvp.sqlite3"
    new_db.parent.mkdir(parents=True)
    assert sqlite_backup.restore(backup_file, new_db) == 2
    restored = build_harness(tmp_path / "new-vps")

    # The two jobs are back, with their data; the one written after the
    # backup is not (that is what "restore to last night" means).
    assert restored.job(first_job)["state"] == "received"
    assert restored.job(second_job)["source"]["sender_jid"].startswith("5511900000002")
    assert restored.client.get(f"/jobs/{late_job}", auth=restored.reviewer_auth).status_code == 404
    close(restored)


def test_restore_refuses_while_the_orchestrator_is_running(tmp_path):
    # A running orchestrator keeps a -wal file next to its DB. Overwriting
    # the DB under it would corrupt both, so restore must refuse.
    running = build_harness(tmp_path / "vps")
    receive_job(running)
    backup_file = tmp_path / "bvp-backup.sqlite3"
    sqlite_backup.backup(live_db_path(running), backup_file)

    with pytest.raises(sqlite_backup.BackupError, match="orchestrator is probably running"):
        sqlite_backup.restore(backup_file, live_db_path(running))
    close(running)


def test_restore_keeps_the_previous_db_aside(tmp_path):
    # If the wrong backup gets restored, the DB it replaced is still there.
    source = build_harness(tmp_path / "vps")
    receive_job(source)
    backup_file = tmp_path / "bvp-backup.sqlite3"
    sqlite_backup.backup(live_db_path(source), backup_file)
    close(source)  # stopped: its -wal is checkpointed and removed

    sqlite_backup.restore(backup_file, live_db_path(source))
    assert live_db_path(source).with_name("bvp.sqlite3.before-restore").exists()


def test_a_damaged_backup_is_refused_before_anything_is_overwritten(tmp_path):
    broken = tmp_path / "broken.sqlite3"
    broken.write_bytes(b"this is not a database" * 100)
    target = tmp_path / "db" / "bvp.sqlite3"
    target.parent.mkdir()
    target.write_bytes(b"current DB")

    with pytest.raises(sqlite_backup.BackupError, match="not a usable"):
        sqlite_backup.restore(broken, target)
    # The current DB was not touched.
    assert target.read_bytes() == b"current DB"


# --- backup.sh with fake commands -------------------------------------------------

linux_only = pytest.mark.skipif(
    sys.platform != "linux" or shutil.which("bash") is None,
    reason="backup.sh is a bash script for the Linux VPS",
)

# Fake `docker`: answers the four kinds of call backup.sh makes with small
# recognisable bytes. FAKE_DOCKER_FAIL=postgres makes the pg_dump fail.
FAKE_DOCKER = """#!{python}
import os, sys
args = sys.argv[1:]
fail = os.environ.get("FAKE_DOCKER_FAIL", "")
if "exec" in args and "postgres" in args:
    if fail == "postgres":
        sys.exit(1)
    sys.stdout.write("PGDMP-fake-dump")
elif "cp" in args:
    open(args[-1], "w").write("REDIS0011-fake-snapshot")
elif "exec" in args:
    pass  # redis-cli --rdb / rm inside the redis container
elif "run" in args:
    sys.stdout.write("fake-instances-tarball")
else:
    sys.exit("fake docker: unexpected call " + " ".join(args))
"""

# Fake `setpriv`: drop the user-switching options and run the command as is
# (the test already runs as the folder's owner).
FAKE_SETPRIV = """#!/bin/sh
while [ "$1" != "--" ]; do shift; done
shift
exec "$@"
"""


def prepare_vps(tmp_path: Path) -> dict[str, str]:
    """A data folder with a live orchestrator DB, fake commands, and the env to run with."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, text in {
        "docker": FAKE_DOCKER.replace("{python}", sys.executable),
        "setpriv": FAKE_SETPRIV,
    }.items():
        (bin_dir / name).write_text(text)
        (bin_dir / name).chmod(0o755)

    harness = build_harness(tmp_path / "srv-pipeline")
    receive_job(harness)
    close(harness)
    (tmp_path / "srv-pipeline" / "teams.csv").write_text("car_number,team_name,school\n")

    return {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "BAJA_BACKUP_ROOT": str(tmp_path / "backups"),
        "BAJA_DATA_DIR": str(tmp_path / "srv-pipeline"),
        "BAJA_BACKUP_UID": str(os.getuid()),
        "BAJA_BACKUP_PYTHON": sys.executable,
    }


def run_backup(environment: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(VPS_FOLDER / "backup.sh")],
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
    )


def backup_folders(environment: dict[str, str]) -> list[Path]:
    root = Path(environment["BAJA_BACKUP_ROOT"])
    return sorted(path for path in root.iterdir() if not path.name.startswith("."))


@linux_only
def test_backup_sh_writes_every_part_with_checksums(tmp_path):
    environment = prepare_vps(tmp_path)

    completed = run_backup(environment)

    assert completed.returncode == 0, completed.stdout + completed.stderr
    [folder] = backup_folders(environment)
    names = sorted(path.name for path in folder.iterdir())
    assert names == [
        "SHA256SUMS",
        "bvp.sqlite3",
        "evolution.dump",
        "evolution.rdb",
        "evolution_instances.tgz",
        "reviewers.txt",
        "teams.csv",
    ]
    # No .env in a backup: secrets are kept elsewhere.
    assert not any(".env" in name for name in names)
    # Only root (here: the owner) may enter it: it holds WhatsApp keys.
    assert folder.stat().st_mode & 0o777 == 0o700
    # The DB copy is a real, intact orchestrator DB with the one job.
    copy = sqlite3.connect(folder / "bvp.sqlite3")
    assert copy.execute("SELECT COUNT(*) FROM jobs").fetchone() == (1,)
    copy.close()
    # Every file is listed in SHA256SUMS with its true hash.
    for line in (folder / "SHA256SUMS").read_text().splitlines():
        digest, name = line.split()
        assert hashlib.sha256((folder / name).read_bytes()).hexdigest() == digest


@linux_only
def test_a_failed_part_marks_the_backup_incomplete_but_keeps_the_rest(tmp_path):
    environment = {**prepare_vps(tmp_path), "FAKE_DOCKER_FAIL": "postgres"}

    completed = run_backup(environment)

    # Exit 1 makes the systemd unit show "failed"...
    assert completed.returncode == 1
    assert "postgres FAILED" in completed.stdout
    # ...and the folder says so in its name, but still has the other parts.
    [folder] = backup_folders(environment)
    assert folder.name.endswith("-incomplete")
    assert (folder / "bvp.sqlite3").exists()
    assert (folder / "evolution.rdb").exists()


@linux_only
def test_only_the_newest_backups_are_kept(tmp_path):
    environment = {**prepare_vps(tmp_path), "BAJA_BACKUP_KEEP": "3"}
    # Five old backups from January 2020 already exist.
    root = Path(environment["BAJA_BACKUP_ROOT"])
    for day in range(1, 6):
        (root / f"2020-01-0{day}T043000Z").mkdir(parents=True)

    completed = run_backup(environment)
    assert completed.returncode == 0

    # The journal says what was dropped: the three oldest.
    assert completed.stdout.count("removing old backup") == 3
    # Three remain: today's and the two newest old ones.
    kept = [path.name for path in backup_folders(environment)]
    assert len(kept) == 3
    assert kept[:2] == ["2020-01-04T043000Z", "2020-01-05T043000Z"]
    assert not kept[2].startswith("2020")
