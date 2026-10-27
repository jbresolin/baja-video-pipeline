"""The SQLite jobs DB: opening it, migrations, and transactions.

One connection is shared by the whole process and guarded by a lock, so
exactly one transaction runs at a time. The orchestrator handles a few
requests per second at most, so this is plenty, and it rules out
"database is locked" errors and interleaved read-modify-write bugs.

Migrations are numbered SQL files in db/migrations/ (0001_initial.sql,
0002_...). At startup every file newer than the DB's `user_version` is
applied in order, inside one transaction each.
"""

import re
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path

# Fixed width, so stored times sort and compare as text.
DB_TIME_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"
MIGRATION_FILE_PATTERN = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")


def to_db_time(moment: datetime | None) -> str | None:
    if moment is None:
        return None
    if moment.tzinfo is None:
        raise ValueError("refusing to store a naive datetime; use UTC")
    return moment.astimezone(UTC).strftime(DB_TIME_FORMAT)


def from_db_time(text: str | None) -> datetime | None:
    if text is None:
        return None
    return datetime.strptime(text, DB_TIME_FORMAT).replace(tzinfo=UTC)


def list_migrations() -> list[tuple[int, str, str]]:
    """(number, file name, SQL text) of every bundled migration, in order."""
    folder = resources.files("orchestrator.db") / "migrations"
    found = []
    for entry in folder.iterdir():
        match = MIGRATION_FILE_PATTERN.match(entry.name)
        if match:
            found.append((int(match.group(1)), entry.name, entry.read_text(encoding="utf-8")))
    found.sort()
    numbers = [number for number, _, _ in found]
    if numbers != list(range(1, len(numbers) + 1)):
        raise RuntimeError(f"migrations must be numbered 0001, 0002, ... without gaps: {numbers}")
    return found


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False: FastAPI runs handlers in a thread pool;
        # the lock below makes sharing the connection safe.
        # isolation_level=None: we issue BEGIN/COMMIT ourselves.
        self._connection = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA foreign_keys=ON")
        # Wait instead of failing if an outside tool (sqlite3 CLI) holds a lock.
        self._connection.execute("PRAGMA busy_timeout=5000")
        self._lock = threading.RLock()

    @property
    def journal_mode(self) -> str:
        return self._connection.execute("PRAGMA journal_mode").fetchone()[0]

    @property
    def schema_version(self) -> int:
        return self._connection.execute("PRAGMA user_version").fetchone()[0]

    def apply_migrations(self) -> list[str]:
        """Apply every migration newer than the DB; return the file names applied."""
        applied = []
        with self._lock:
            for number, name, sql in list_migrations():
                if number <= self.schema_version:
                    continue
                # executescript() commits first and runs the file as-is, so the
                # BEGIN/COMMIT around it makes each file all-or-nothing.
                try:
                    self._connection.executescript(
                        f"BEGIN IMMEDIATE;\n{sql}\nPRAGMA user_version = {number};\nCOMMIT;"
                    )
                except sqlite3.Error as error:
                    if self._connection.in_transaction:
                        self._connection.execute("ROLLBACK")
                    raise RuntimeError(f"migration {name} failed: {error}") from error
                applied.append(name)
        return applied

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Run the block as one transaction: committed on success, rolled back on error."""
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                yield self._connection
            except BaseException:
                self._connection.execute("ROLLBACK")
                raise
            self._connection.execute("COMMIT")

    def close(self) -> None:
        with self._lock:
            self._connection.close()


def open_database(data_dir: Path) -> Database:
    """Open (creating if needed) BVP_DATA_DIR/db/bvp.sqlite3 and bring its schema up to date."""
    database = Database(data_dir / "db" / "bvp.sqlite3")
    database.apply_migrations()
    return database
