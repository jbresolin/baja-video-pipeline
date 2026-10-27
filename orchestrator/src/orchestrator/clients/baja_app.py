"""Team names by car number, from the Baja app (BVP_BAJA_APP_URL), cached in memory.

Assumed endpoint (an open question in the PR; adjust TEAM_PATH when the
app's API is confirmed):

    GET {BVP_BAJA_APP_URL}/api/teams/{car_number}
        Authorization: Bearer <BVP_BAJA_APP_TOKEN>   (only if set)
    200 {"car_number": 42, "team_name": "Equipe X", "school": "..."}
    404 unknown car

The reply is read leniently (`team_name`, `name` or `equipe`; `school` or
`escola`). Found teams are cached for 10 minutes and unknown cars for one,
so typing a car number in the review UI does not hammer the app.
"""

import threading
from datetime import datetime, timedelta

import httpx

from orchestrator.clock import Clock
from shared.models import Team

TEAM_PATH = "/api/teams/{car_number}"
FOUND_CACHE_SECONDS = 600
NOT_FOUND_CACHE_SECONDS = 60


class BajaAppError(Exception):
    """The Baja app could not be asked (network error, 5xx, unreadable answer)."""


class BajaAppClient:
    def __init__(self, http_client: httpx.Client, base_url: str, token: str, clock: Clock) -> None:
        self._http = http_client
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._clock = clock
        # car_number → (team or None for "unknown car", valid until)
        self._cache: dict[int, tuple[Team | None, datetime]] = {}
        self._lock = threading.Lock()

    def find_team(self, car_number: int) -> Team | None:
        """The car's team, None if the app does not know the car. Raises BajaAppError."""
        now = self._clock.now()
        with self._lock:
            cached = self._cache.get(car_number)
            if cached is not None and now < cached[1]:
                return cached[0]

        team = self._fetch(car_number)
        lifetime = FOUND_CACHE_SECONDS if team else NOT_FOUND_CACHE_SECONDS
        with self._lock:
            self._cache[car_number] = (team, now + timedelta(seconds=lifetime))
        return team

    def _fetch(self, car_number: int) -> Team | None:
        headers = {"Authorization": f"Bearer {self._token}"} if self._token else {}
        url = self._base_url + TEAM_PATH.format(car_number=car_number)
        try:
            response = self._http.get(url, headers=headers)
        except httpx.HTTPError as error:
            raise BajaAppError(f"cannot reach the Baja app: {error}") from error
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise BajaAppError(f"Baja app answered HTTP {response.status_code}")
        try:
            body = response.json()
            team_name = body.get("team_name") or body.get("name") or body.get("equipe")
            school = body.get("school") or body.get("escola")
        except (ValueError, AttributeError) as error:
            raise BajaAppError("Baja app answer is not a JSON object") from error
        if not team_name:
            raise BajaAppError("Baja app answer has no team name")
        return Team(car_number=car_number, team_name=str(team_name), school=school)
