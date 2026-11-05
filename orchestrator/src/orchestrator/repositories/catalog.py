"""SQL for the provas list and the teams table (the review form's choices)."""

import sqlite3
from datetime import datetime

from orchestrator.db.database import to_db_time
from shared.models import Prova, Team

# --- Provas ---


def list_prova_rows(connection: sqlite3.Connection) -> list[sqlite3.Row]:
    return connection.execute("SELECT * FROM provas ORDER BY display_order, id").fetchall()


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


def insert_prova(connection: sqlite3.Connection, name: str, slug: str, now: datetime) -> int:
    """Add an active prova at the end of the list; returns its id."""
    cursor = connection.execute(
        "INSERT INTO provas (name, slug, display_order, active, created_at, updated_at)"
        " VALUES (?, ?, ?, 1, ?, ?)",
        (name, slug, next_display_order(connection), to_db_time(now), to_db_time(now)),
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
    )


# --- Teams ---


def list_teams(connection: sqlite3.Connection) -> list[Team]:
    rows = connection.execute("SELECT * FROM teams ORDER BY car_number").fetchall()
    return [_team_from_row(row) for row in rows]


def get_team(connection: sqlite3.Connection, car_number: int) -> Team | None:
    row = connection.execute("SELECT * FROM teams WHERE car_number = ?", (car_number,)).fetchone()
    return _team_from_row(row) if row else None


def count_teams(connection: sqlite3.Connection) -> int:
    return connection.execute("SELECT COUNT(*) FROM teams").fetchone()[0]


def save_team(connection: sqlite3.Connection, team: Team, now: datetime) -> None:
    """Insert the car, or replace its team name and school."""
    connection.execute(
        "INSERT INTO teams (car_number, team_name, school, updated_at) VALUES (?, ?, ?, ?)"
        " ON CONFLICT (car_number) DO UPDATE SET team_name = excluded.team_name,"
        " school = excluded.school, updated_at = excluded.updated_at",
        (team.car_number, team.team_name, team.school, to_db_time(now)),
    )


def delete_team(connection: sqlite3.Connection, car_number: int) -> bool:
    return connection.execute("DELETE FROM teams WHERE car_number = ?", (car_number,)).rowcount == 1


def delete_all_teams(connection: sqlite3.Connection) -> int:
    return connection.execute("DELETE FROM teams").rowcount


def _team_from_row(row: sqlite3.Row) -> Team:
    return Team(car_number=row["car_number"], team_name=row["team_name"], school=row["school"])
