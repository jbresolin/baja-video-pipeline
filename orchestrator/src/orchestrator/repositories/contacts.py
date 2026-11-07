"""SQL for WhatsApp senders (the admin Contacts page)."""

import sqlite3
from datetime import datetime

from orchestrator.db.database import from_db_time, to_db_time
from shared.models import Contact, ContactStatus

# The contact plus its default prova's name, for display.
_SELECT_WITH_PROVA = (
    "SELECT contacts.*, provas.name AS default_prova_name FROM contacts"
    " LEFT JOIN provas ON provas.id = contacts.default_prova_id"
)


def get_contact_row(connection: sqlite3.Connection, phone: str) -> sqlite3.Row | None:
    return connection.execute(f"{_SELECT_WITH_PROVA} WHERE contacts.phone = ?", (phone,)).fetchone()


def list_contact_rows(
    connection: sqlite3.Connection, status: ContactStatus | None = None
) -> list[sqlite3.Row]:
    """Newest activity first; contacts that never wrote come last, newest added first."""
    where, parameters = ("WHERE contacts.status = ?", [str(status)]) if status else ("", [])
    return connection.execute(
        f"{_SELECT_WITH_PROVA} {where} ORDER BY contacts.last_seen_at IS NULL,"
        " contacts.last_seen_at DESC, contacts.created_at DESC",
        parameters,
    ).fetchall()


def insert_contact(
    connection: sqlite3.Connection,
    phone: str,
    now: datetime,
    *,
    status: ContactStatus,
    display_name: str | None = None,
    push_name: str | None = None,
    default_prova_id: int | None = None,
    notes: str | None = None,
    seen: bool = False,
) -> None:
    """Add a contact. `seen=True` when it comes from a message (first/last seen = now)."""
    seen_at = to_db_time(now) if seen else None
    connection.execute(
        "INSERT INTO contacts (phone, display_name, push_name, status, default_prova_id, notes,"
        " first_seen_at, last_seen_at, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            phone,
            display_name,
            push_name,
            str(status),
            default_prova_id,
            notes,
            seen_at,
            seen_at,
            to_db_time(now),
            to_db_time(now),
        ),
    )


def update_contact(
    connection: sqlite3.Connection, phone: str, now: datetime, changes: dict
) -> None:
    """Set the given columns and updated_at."""
    columns = {**changes, "updated_at": to_db_time(now)}
    assignments = ", ".join(f"{name} = ?" for name in columns)
    connection.execute(
        f"UPDATE contacts SET {assignments} WHERE phone = ?", [*columns.values(), phone]
    )


def contact_from_row(row: sqlite3.Row) -> Contact:
    return Contact(
        phone=row["phone"],
        display_name=row["display_name"],
        push_name=row["push_name"],
        status=ContactStatus(row["status"]),
        default_prova_id=row["default_prova_id"],
        default_prova=row["default_prova_name"],
        notes=row["notes"],
        first_seen_at=from_db_time(row["first_seen_at"]),
        last_seen_at=from_db_time(row["last_seen_at"]),
        welcome_sent_at=from_db_time(row["welcome_sent_at"]),
    )
