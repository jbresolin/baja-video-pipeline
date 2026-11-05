"""Team names by car number: the teams table first, then the Baja app.

    1. The `teams` table, edited on the admin Teams page (inline edits or a
       CSV upload `car_number,team_name,school`). BVP_TEAMS_FILE is
       imported into it once, on the first start with an empty table
       (domain/first_start.py), and ignored afterwards.
    2. BVP_BAJA_APP_URL: the Baja app, through BajaAppClient (optional).

A car neither knows is "unknown": find_team returns None (GET /teams
answers 404 and the job's team_name stays empty).
"""

import csv
from dataclasses import dataclass, field

from orchestrator.clients.baja_app import BajaAppClient
from orchestrator.db.database import Database
from orchestrator.repositories import catalog as catalog_repo
from shared.models import Team

TEAMS_FILE_COLUMNS = ("car_number", "team_name", "school")
# Excel puts this invisible mark in front of UTF-8 files it saves.
BYTE_ORDER_MARK = "﻿"


@dataclass
class ParsedTeams:
    """The result of reading a teams CSV: the teams, and one message per skipped line."""

    teams: dict[int, Team] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)


def parse_teams_csv(text: str) -> ParsedTeams:
    """car_number → Team from the CSV text (header line optional). Bad lines are reported."""
    parsed = ParsedTeams()
    text = text.removeprefix(BYTE_ORDER_MARK)
    for line_number, fields in enumerate(csv.reader(text.splitlines()), start=1):
        cells = [cell.strip() for cell in fields]
        if not any(cells) or cells[0].startswith("#"):
            continue  # blank line or comment
        if line_number == 1 and cells[0].lower() == "car_number":
            continue  # the optional header
        if len(cells) < 2 or not cells[1]:
            parsed.problems.append(f"line {line_number}: needs car_number,team_name")
            continue
        try:
            car_number = int(cells[0])
            school = cells[2] if len(cells) > 2 and cells[2] else None
            parsed.teams[car_number] = Team(
                car_number=car_number, team_name=cells[1], school=school
            )
        except ValueError:  # not a number, or out of the CarNumber range (0–9999)
            parsed.problems.append(
                f"line {line_number}: car_number {cells[0]!r} is not a number from 0 to 9999"
            )
    return parsed


class TeamDirectory:
    """The teams table first, then the Baja app, then unknown (None)."""

    def __init__(self, database: Database, baja_app: BajaAppClient | None) -> None:
        self._database = database
        self.baja_app = baja_app

    def find_team(self, car_number: int) -> Team | None:
        """Raises BajaAppError only when the table does not know the car and the app fails."""
        with self._database.transaction() as connection:
            team = catalog_repo.get_team(connection, car_number)
        if team is not None:
            return team
        if self.baja_app is not None:
            return self.baja_app.find_team(car_number)
        return None
