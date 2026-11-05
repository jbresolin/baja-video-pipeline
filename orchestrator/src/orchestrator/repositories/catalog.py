"""SQL for the provas list (the review form's choices). Teams: repositories/teams.py."""

import sqlite3
from datetime import datetime

from orchestrator.db.database import to_db_time
from shared.models import CatalogSource, Prova

# --- Provas ---


def list_prova_rows(connection: sqlite3.Connection) -> list[sqlite3.Row]:
    """Synced provas first (the API's order), then manual ones (the admin's order)."""
    return connection.execute(
        "SELECT * FROM provas ORDER BY source = 'manual', display_order, id"
    ).fetchall()


def list_synced_prova_rows(connection: sqlite3.Connection) -> list[sqlite3.Row]:
    return connection.execute(
        "SELECT * FROM provas WHERE source = ?", (str(CatalogSource.RESULTS_API),)
    ).fetchall()


def find_synced_prova_row(connection: sqlite3.Connection, external_id: str) -> sqlite3.Row | None:
    return connection.execute(
        "SELECT * FROM provas WHERE source = ? AND external_id = ?",
        (str(CatalogSource.RESULTS_API), external_id),
    ).fetchone()


def get_prova_row(connection: sqlite3.Connection, prova_id: int) -> sqlite3.Row | None:
    return connection.execute("SELECT * FROM provas WHERE id = ?", (prova_id,)).fetchone()


def find_prova_row_by_name(connection: sqlite3.Connection, name: str) -> sqlite3.Row | None:
    return connection.execute("SELECT * FROM provas WHERE name = ?", (name,)).fetchone()


def find_prova_row_by_slug(connection: sqlite3.Connection, slug: str) -> sqlite3.Row | None:
    return connection.execute("SELECT * FROM provas WHERE slug = ?", (slug,)).fetchone()


def next_display_order(connection: sqlite3.Connection) -> int:
    return connection.execute("SELECT COALESCE(MAX(display_order), 0) + 1 FROM provas").fetchone()[
        0
    ]


def insert_prova(
    connection: sqlite3.Connection,
    name: str,
    slug: str,
    now: datetime,
    *,
    source: CatalogSource = CatalogSource.MANUAL,
    external_id: str | None = None,
    display_order: int | None = None,
) -> int:
    """Add an active prova (at the end of the list by default); returns its id."""
    order = display_order if display_order is not None else next_display_order(connection)
    cursor = connection.execute(
        "INSERT INTO provas (name, slug, display_order, active, created_at, updated_at,"
        " source, external_id) VALUES (?, ?, ?, 1, ?, ?, ?, ?)",
        (name, slug, order, to_db_time(now), to_db_time(now), str(source), external_id),
    )
    return cursor.lastrowid


def update_prova(
    connection: sqlite3.Connection, prova_id: int, now: datetime, changes: dict
) -> None:
    """Set the given columns (name, active, display_order) and updated_at."""
    columns = {**changes, "updated_at": to_db_time(now)}
    assignments = ", ".join(f"{name} = ?" for name in columns)
    connection.execute(
        f"UPDATE provas SET {assignments} WHERE id = ?", [*columns.values(), prova_id]
    )


def rename_prova_on_jobs(connection: sqlite3.Connection, old_name: str, new_name: str) -> int:
    """Relabel every job that has the old prova name; returns how many."""
    return connection.execute(
        "UPDATE jobs SET prova = ? WHERE prova = ?", (new_name, old_name)
    ).rowcount


def distinct_job_provas(connection: sqlite3.Connection) -> list[str]:
    """Every prova name used by a job, oldest job first."""
    rows = connection.execute(
        "SELECT prova, MIN(created_at) AS first_used FROM jobs WHERE prova IS NOT NULL"
        " GROUP BY prova ORDER BY first_used, prova"
    ).fetchall()
    return [row["prova"] for row in rows]


def prova_from_row(row: sqlite3.Row) -> Prova:
    return Prova(
        id=row["id"],
        name=row["name"],
        slug=row["slug"],
        display_order=row["display_order"],
        active=bool(row["active"]),
        source=CatalogSource(row["source"]),
        external_id=row["external_id"],
    )
