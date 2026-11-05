"""SQL for review UI users and their sign-in sessions."""

import sqlite3
from datetime import datetime

from orchestrator.db.database import from_db_time, to_db_time
from shared.models import User, UserRole


def count_users(connection: sqlite3.Connection) -> int:
    return connection.execute("SELECT COUNT(*) FROM users").fetchone()[0]


def get_user_row(connection: sqlite3.Connection, username: str) -> sqlite3.Row | None:
    return connection.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()


def list_user_rows(connection: sqlite3.Connection) -> list[sqlite3.Row]:
    return connection.execute("SELECT * FROM users ORDER BY username").fetchall()


def insert_user(
    connection: sqlite3.Connection,
    username: str,
    password_hash: str,
    role: UserRole,
    now: datetime,
) -> None:
    connection.execute(
        "INSERT INTO users (username, password_hash, role, disabled, created_at, updated_at)"
        " VALUES (?, ?, ?, 0, ?, ?)",
        (username, password_hash, str(role), to_db_time(now), to_db_time(now)),
    )


def update_user(
    connection: sqlite3.Connection, username: str, now: datetime, changes: dict
) -> None:
    """Set the given columns (role, disabled, password_hash) and updated_at."""
    columns = {**changes, "updated_at": to_db_time(now)}
    assignments = ", ".join(f"{name} = ?" for name in columns)
    connection.execute(
        f"UPDATE users SET {assignments} WHERE username = ?", [*columns.values(), username]
    )


def count_enabled_admins(connection: sqlite3.Connection) -> int:
    return connection.execute(
        "SELECT COUNT(*) FROM users WHERE role = ? AND disabled = 0", (str(UserRole.ADMIN),)
    ).fetchone()[0]


def user_from_row(row: sqlite3.Row) -> User:
    return User(
        username=row["username"],
        role=UserRole(row["role"]),
        disabled=bool(row["disabled"]),
        created_at=from_db_time(row["created_at"]),
        updated_at=from_db_time(row["updated_at"]),
    )


# --- Sessions ---


def insert_session(
    connection: sqlite3.Connection, token_sha256: str, username: str, now: datetime
) -> None:
    connection.execute(
        "INSERT INTO sessions (token_sha256, username, created_at, last_seen_at)"
        " VALUES (?, ?, ?, ?)",
        (token_sha256, username, to_db_time(now), to_db_time(now)),
    )


def find_session_with_user(connection: sqlite3.Connection, token_sha256: str) -> sqlite3.Row | None:
    """The session joined with its user (role, disabled), or None."""
    return connection.execute(
        "SELECT sessions.token_sha256, sessions.last_seen_at, users.username, users.role,"
        " users.disabled FROM sessions JOIN users ON users.username = sessions.username"
        " WHERE sessions.token_sha256 = ?",
        (token_sha256,),
    ).fetchone()


def touch_session(connection: sqlite3.Connection, token_sha256: str, now: datetime) -> None:
    connection.execute(
        "UPDATE sessions SET last_seen_at = ? WHERE token_sha256 = ?",
        (to_db_time(now), token_sha256),
    )


def delete_session(connection: sqlite3.Connection, token_sha256: str) -> None:
    connection.execute("DELETE FROM sessions WHERE token_sha256 = ?", (token_sha256,))


def delete_sessions_of_user(connection: sqlite3.Connection, username: str) -> int:
    return connection.execute("DELETE FROM sessions WHERE username = ?", (username,)).rowcount


def delete_sessions_idle_since(connection: sqlite3.Connection, cutoff: datetime) -> int:
    """Remove sessions whose last request was before `cutoff`; returns how many."""
    return connection.execute(
        "DELETE FROM sessions WHERE last_seen_at < ?", (to_db_time(cutoff),)
    ).rowcount
