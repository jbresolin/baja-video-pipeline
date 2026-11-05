"""SQL for the teams table: car number → team, synced from the results API or typed.

For a synced team (source results_api) team_name / short_name / school are
the API's values and override_* hold an admin's correction, which the sync
never touches. For a manual team the plain columns are the admin's values.
What lookups answer is the override when there is one, else the plain value.
"""

import sqlite3
from datetime import datetime

from orchestrator.db.database import to_db_time
from shared.models import CatalogSource, Team, TeamRow


def get_team_row(connection: sqlite3.Connection, car_number: int) -> sqlite3.Row | None:
    return connection.execute("SELECT * FROM teams WHERE car_number = ?", (car_number,)).fetchone()


def list_team_rows(connection: sqlite3.Connection) -> list[sqlite3.Row]:
    return connection.execute("SELECT * FROM teams ORDER BY car_number").fetchall()


def count_teams(connection: sqlite3.Connection) -> int:
    return connection.execute("SELECT COUNT(*) FROM teams").fetchone()[0]


def save_manual_team(connection: sqlite3.Connection, team: Team, now: datetime) -> None:
    """Insert or replace a manual team (a car the results API does not list)."""
    connection.execute(
        "INSERT INTO teams (car_number, team_name, short_name, school, updated_at, source)"
        " VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (car_number) DO UPDATE SET"
        " team_name = excluded.team_name, short_name = excluded.short_name,"
        " school = excluded.school, updated_at = excluded.updated_at",
        (
            team.car_number,
            team.team_name,
            team.short_name,
            team.school,
            to_db_time(now),
            str(CatalogSource.MANUAL),
        ),
    )


def save_synced_team(connection: sqlite3.Connection, team: Team, now: datetime) -> None:
    """Insert or update a team from the API. Overrides are left alone."""
    connection.execute(
        "INSERT INTO teams (car_number, team_name, short_name, school, updated_at, source,"
        " in_source) VALUES (?, ?, ?, ?, ?, ?, 1) ON CONFLICT (car_number) DO UPDATE SET"
        " team_name = excluded.team_name, short_name = excluded.short_name,"
        " school = excluded.school, updated_at = excluded.updated_at,"
        " source = excluded.source, in_source = 1",
        (
            team.car_number,
            team.team_name,
            team.short_name,
            team.school,
            to_db_time(now),
            str(CatalogSource.RESULTS_API),
        ),
    )


def set_override(
    connection: sqlite3.Connection, car_number: int, team: Team | None, now: datetime
) -> None:
    """Store (or with None, clear) an admin's correction of a synced team."""
    values = (team.team_name, team.short_name, team.school) if team else (None, None, None)
    connection.execute(
        "UPDATE teams SET override_team_name = ?, override_short_name = ?, override_school = ?,"
        " updated_at = ? WHERE car_number = ?",
        (*values, to_db_time(now), car_number),
    )


def mark_synced_teams_missing(
    connection: sqlite3.Connection, listed: set[int], now: datetime
) -> int:
    """Synced teams the API stopped listing: in_source = 0 (kept, never deleted)."""
    count = 0
    for row in connection.execute(
        "SELECT car_number FROM teams WHERE source = ? AND in_source = 1",
        (str(CatalogSource.RESULTS_API),),
    ).fetchall():
        if row["car_number"] not in listed:
            connection.execute(
                "UPDATE teams SET in_source = 0, updated_at = ? WHERE car_number = ?",
                (to_db_time(now), row["car_number"]),
            )
            count += 1
    return count


def delete_team(connection: sqlite3.Connection, car_number: int) -> bool:
    return connection.execute("DELETE FROM teams WHERE car_number = ?", (car_number,)).rowcount == 1


def team_from_row(row: sqlite3.Row) -> Team:
    """What a lookup answers: the admin's correction where there is one."""
    return Team(
        car_number=row["car_number"],
        team_name=row["override_team_name"] or row["team_name"],
        short_name=row["override_short_name"] or row["short_name"],
        school=row["override_school"] or row["school"],
    )


def team_row_from_row(row: sqlite3.Row) -> TeamRow:
    effective = team_from_row(row)
    synced = row["source"] == CatalogSource.RESULTS_API
    return TeamRow(
        car_number=effective.car_number,
        team_name=effective.team_name,
        short_name=effective.short_name,
        school=effective.school,
        source=CatalogSource(row["source"]),
        in_source=bool(row["in_source"]) if synced else True,
        overridden=row["override_team_name"] is not None,
        synced_team_name=row["team_name"] if synced else None,
        synced_short_name=row["short_name"] if synced else None,
        synced_school=row["school"] if synced else None,
    )


def get_team(connection: sqlite3.Connection, car_number: int) -> Team | None:
    row = get_team_row(connection, car_number)
    return team_from_row(row) if row else None
