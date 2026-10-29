#!/usr/bin/env python3
"""Back up and restore the orchestrator's SQLite DB (db/bvp.sqlite3) safely.

    sqlite_backup.py backup  /srv/pipeline/db/bvp.sqlite3  /path/to/bvp.sqlite3
    sqlite_backup.py restore /var/backups/baja/<stamp>/bvp.sqlite3  /srv/pipeline/db/bvp.sqlite3

`backup` uses SQLite's online backup API (what the sqlite3 shell's
`.backup` command does), so it is consistent even while the orchestrator
is writing: copying the file with `cp` could catch a half-applied
transaction or miss what is still in the -wal file. The copy is then
checked with `PRAGMA integrity_check`.

`restore` refuses to run while the orchestrator has the DB open (its
`-wal` file exists), checks the backup first, and writes it into place.

backup.sh runs this as uid 10001 (setpriv), the orchestrator's own user,
so any -shm/-wal file SQLite creates is not left owned by root.
Standard library only.
"""

import argparse
import sqlite3
import sys
from pathlib import Path


class BackupError(Exception):
    """Something is wrong with a backup or restore. The message is for a human."""


def check_integrity(path: Path) -> int:
    """Raise BackupError unless the DB at `path` is intact; return its job count."""
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        verdict = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if verdict != "ok":
            raise BackupError(f"{path}: integrity check failed: {verdict}")
        return connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    except sqlite3.DatabaseError as error:
        raise BackupError(f"{path}: not a usable orchestrator DB: {error}") from error
    finally:
        connection.close()


def copy_database(source: Path, destination: Path) -> None:
    """SQLite online backup of `source` into a new file at `destination`."""
    destination.unlink(missing_ok=True)
    source_connection = sqlite3.connect(source)
    destination_connection = sqlite3.connect(destination)
    try:
        source_connection.backup(destination_connection)
        # A single self-contained file, readable without a -wal beside it.
        destination_connection.execute("PRAGMA journal_mode=DELETE")
    finally:
        destination_connection.close()
        source_connection.close()


def backup(database: Path, destination: Path) -> int:
    """Back up the live DB; returns the number of jobs in the copy."""
    if not database.exists():
        raise BackupError(f"{database} does not exist")
    copy_database(database, destination)
    return check_integrity(destination)


def restore(backup_file: Path, database: Path) -> int:
    """Put a backup in place of the (stopped) orchestrator's DB; returns its job count."""
    wal_file = database.with_name(database.name + "-wal")
    if wal_file.exists():
        raise BackupError(
            f"{wal_file} exists: the orchestrator is probably running. "
            "Stop it first: docker compose stop orchestrator"
        )
    jobs = check_integrity(backup_file)
    # Keep what was there, in case this was the wrong backup.
    if database.exists():
        database.replace(database.with_name(database.name + ".before-restore"))
    database.with_name(database.name + "-shm").unlink(missing_ok=True)
    copy_database(backup_file, database)
    # The orchestrator opens it in WAL mode again on start (db/database.py).
    return jobs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("action", choices=["backup", "restore"])
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    arguments = parser.parse_args(argv)
    try:
        if arguments.action == "backup":
            jobs = backup(arguments.source, arguments.destination)
        else:
            jobs = restore(arguments.source, arguments.destination)
    except BackupError as error:
        print(f"sqlite_backup: {error}", file=sys.stderr)
        return 1
    print(f"{arguments.action}: {arguments.destination} ok, {jobs} job(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
