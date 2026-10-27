"""Team names by car number from up to two optional sources (design §17, "Team lookup").

    1. BVP_TEAMS_FILE: a CSV the organisers keep on the VPS,
           car_number,team_name,school
           42,Equipe Trovão,UFSC
       (the header line is optional; `school` may be empty). The file is
       read again whenever its modification time or size changes, so an
       edit takes effect without a restart.
    2. BVP_BAJA_APP_URL: the Baja app, through BajaAppClient.

The file is asked first, then the app. A car neither knows is "unknown":
find_team returns None (GET /teams answers 404 and the job's team_name
stays empty). Neither source is required; with none set every car is
unknown.
"""

import csv
import logging
import threading
from pathlib import Path

from orchestrator.clients.baja_app import BajaAppClient
from shared.models import Team

logger = logging.getLogger(__name__)

TEAMS_FILE_COLUMNS = ("car_number", "team_name", "school")


def parse_teams_csv(text: str) -> dict[int, Team]:
    """car_number → Team from the CSV text. Bad lines are logged and skipped."""
    teams: dict[int, Team] = {}
    for line_number, fields in enumerate(csv.reader(text.splitlines()), start=1):
        cells = [cell.strip() for cell in fields]
        if not any(cells) or cells[0].startswith("#"):
            continue  # blank line or comment
        if line_number == 1 and cells[0].lower() == "car_number":
            continue  # the optional header
        if len(cells) < 2 or not cells[1]:
            logger.warning("teams file line %d: needs car_number,team_name", line_number)
            continue
        try:
            car_number = int(cells[0])
            school = cells[2] if len(cells) > 2 and cells[2] else None
            teams[car_number] = Team(car_number=car_number, team_name=cells[1], school=school)
        except ValueError as error:  # not a number, or out of the CarNumber range
            logger.warning("teams file line %d: %s", line_number, error)
    return teams


class TeamsFile:
    """The CSV, re-read when it changes on disk. A missing file means "no teams"."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._teams: dict[int, Team] = {}
        # (modification time in ns, size) of the version in self._teams.
        self._loaded_signature: tuple[int, int] | None = None
        self._lock = threading.Lock()

    def find_team(self, car_number: int) -> Team | None:
        with self._lock:
            self._reload_if_changed()
            return self._teams.get(car_number)

    def _reload_if_changed(self) -> None:
        try:
            status = self._path.stat()
        except FileNotFoundError:
            self._teams, self._loaded_signature = {}, None
            return
        signature = (status.st_mtime_ns, status.st_size)
        if signature == self._loaded_signature:
            return
        try:
            text = self._path.read_text(encoding="utf-8-sig")  # tolerate Excel's BOM
        except OSError as error:
            logger.warning("cannot read teams file %s: %s", self._path, error)
            return
        self._teams = parse_teams_csv(text)
        self._loaded_signature = signature
        logger.info("loaded %d teams from %s", len(self._teams), self._path)


class TeamDirectory:
    """File first, then the Baja app, then unknown (None)."""

    def __init__(self, teams_file: TeamsFile | None, baja_app: BajaAppClient | None) -> None:
        self.teams_file = teams_file
        self.baja_app = baja_app

    def find_team(self, car_number: int) -> Team | None:
        """Raises BajaAppError only when the file does not know the car and the app fails."""
        if self.teams_file is not None:
            team = self.teams_file.find_team(car_number)
            if team is not None:
                return team
        if self.baja_app is not None:
            return self.baja_app.find_team(car_number)
        return None
