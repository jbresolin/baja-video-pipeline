"""Reading the old teams CSV (BVP_TEAMS_FILE): `car_number,team_name,school`.

Teams now live in the teams table, synced from the results API
(domain/results_sync.py). The CSV is only imported once into an empty
table on the first start (domain/first_start.py), for a VPS that had one.
"""

import csv
from dataclasses import dataclass, field

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
