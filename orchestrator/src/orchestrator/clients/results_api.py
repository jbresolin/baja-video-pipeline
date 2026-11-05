"""The Baja platform's public results API: an event's provas and teams.

    GET {base}/resultados/evento.php?evt=26BR
        → {"provas": [{"id", "name", "type", "status"}, ...], ...}
    GET {base}/resultados/equipes.php?evt=26BR
        → {"teams": [{"number", "name", "short_name", "school", "school_short", ...}, ...]}

Contract: docs/results-api.md in tiagomartines11/baja-sae-brasil-online.
Things that matter here:

* The query string is strict: exactly `evt`, nothing else (an unknown
  parameter is a 400), and the id is sent as typed, case included.
* Errors look like {"success": false, "error": "...", "code": "not_found"};
  a wrong event id is 404 `not_found`.
* 120 requests a minute per client; over it, 429 with `Retry-After`.
* Names are "as stored": they may carry a trailing space or an HTML entity
  the organisers typed, so they are cleaned before use.
* Fields are only ever added: unknown fields are ignored.
"""

import html
import re
from dataclasses import dataclass

import httpx

# What the API accepts as an event id (docs/results-api.md, "Query string").
EVENT_ID = re.compile(r"^[A-Za-z0-9]{4}$")


class ResultsApiError(Exception):
    """The results API could not answer. `code` is its error code, or ours."""

    def __init__(self, message: str, code: str, retry_after_s: int | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.retry_after_s = retry_after_s


@dataclass(frozen=True)
class ApiProva:
    prova_id: str
    name: str
    type: str | None
    status: str | None


@dataclass(frozen=True)
class ApiTeam:
    car_number: int
    name: str
    short_name: str | None
    school: str | None
    school_short: str | None = None


def clean_name(text: object) -> str | None:
    """'Procobaja ' → 'Procobaja'; 'Tra&ccedil;&atilde;o' → 'Tração'; '' → None."""
    if not isinstance(text, str):
        return None
    cleaned = " ".join(html.unescape(text).split())
    return cleaned or None


class ResultsApiClient:
    def __init__(self, http_client: httpx.Client) -> None:
        self._http = http_client

    def _get(self, base_url: str, script: str, event_id: str) -> dict:
        if not EVENT_ID.match(event_id):
            raise ResultsApiError(
                f"{event_id!r} is not an event id (4 letters or digits)", "invalid_parameter"
            )
        url = f"{base_url.rstrip('/')}/resultados/{script}"
        try:
            response = self._http.get(url, params={"evt": event_id})
        except httpx.HTTPError as error:
            raise ResultsApiError(f"results API unreachable: {error}", "unreachable") from error
        if response.status_code == 429:
            retry_after = response.headers.get("Retry-After", "")
            seconds = int(retry_after) if retry_after.isdigit() else None
            raise ResultsApiError(
                f"results API rate limit; try again in {seconds or '?'} s", "rate_limited", seconds
            )
        if response.is_error:
            code, message = "http_error", f"HTTP {response.status_code}"
            try:
                body = response.json()
                code, message = body.get("code") or code, body.get("error") or message
            except ValueError:
                pass
            if code == "not_found":
                message = f"event {event_id} not found in the results API (case matters)"
            raise ResultsApiError(message, code)
        try:
            return response.json()
        except ValueError as error:
            raise ResultsApiError(
                "results API sent something that is not JSON", "bad_answer"
            ) from error

    def event_provas(self, base_url: str, event_id: str) -> list[ApiProva]:
        """The event's provas, in the API's order. Entries without an id or name are skipped."""
        body = self._get(base_url, "evento.php", event_id)
        provas = []
        for entry in body.get("provas") or []:
            if not isinstance(entry, dict):
                continue
            prova_id, name = clean_name(entry.get("id")), clean_name(entry.get("name"))
            if prova_id and name:
                provas.append(ApiProva(prova_id, name, entry.get("type"), entry.get("status")))
        return provas

    def event_teams(self, base_url: str, event_id: str) -> list[ApiTeam]:
        """The event's teams by car number. Entries without a number or name are skipped."""
        body = self._get(base_url, "equipes.php", event_id)
        teams = []
        for entry in body.get("teams") or []:
            if not isinstance(entry, dict):
                continue
            number, name = entry.get("number"), clean_name(entry.get("name"))
            if not isinstance(number, int) or isinstance(number, bool) or not 0 <= number <= 9999:
                continue
            if name:
                teams.append(
                    ApiTeam(
                        car_number=number,
                        name=name,
                        short_name=clean_name(entry.get("short_name")),
                        school=clean_name(entry.get("school")),
                        school_short=clean_name(entry.get("school_short")),
                    )
                )
        return teams
