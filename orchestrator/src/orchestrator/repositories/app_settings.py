"""SQL for the admin settings and the record of one-time startup imports."""

import json
import sqlite3
from datetime import datetime
from typing import Any

from orchestrator.db.database import to_db_time


def load_settings_values(connection: sqlite3.Connection) -> dict[str, Any]:
    """Every stored setting, key → value (decoded from JSON)."""
    rows = connection.execute("SELECT key, value_json FROM app_settings").fetchall()
    return {row["key"]: json.loads(row["value_json"]) for row in rows}


def save_setting(connection: sqlite3.Connection, key: str, value: Any, now: datetime) -> None:
    connection.execute(
        "INSERT INTO app_settings (key, value_json, updated_at) VALUES (?, ?, ?)"
        " ON CONFLICT (key) DO UPDATE SET value_json = excluded.value_json,"
        " updated_at = excluded.updated_at",
        (key, json.dumps(value), to_db_time(now)),
    )


def import_was_done(connection: sqlite3.Connection, name: str) -> bool:
    row = connection.execute("SELECT 1 FROM first_start_imports WHERE name = ?", (name,)).fetchone()
    return row is not None


def mark_import_done(connection: sqlite3.Connection, name: str, now: datetime, detail: str) -> None:
    connection.execute(
        "INSERT INTO first_start_imports (name, done_at, detail) VALUES (?, ?, ?)",
        (name, to_db_time(now), detail),
    )
