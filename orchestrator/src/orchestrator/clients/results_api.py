"""The Baja platform's public results API: an event's provas and teams.

    GET {base}/resultados/evento.php?evt=26BR
        → {"provas": [{"id", "name", "type", "status"}, ...], ...}
    GET {base}/resultados/equipes.php?evt=26BR
        → {"teams": [{"number", "name", "short_name", "school", "school_short", ...}, ...]}
    GET {base}/resultados/resultado.php?evt=26BR&id=26BR_MAN&display=1
        → {"columns": [...], "rows": [{"team": {...}, "cells": [...]}, ...]}
          (one results table; domain/results_table.py reads it, LIVESTACK-47)

Contract: docs/results-api.md in tiagomartines11/baja-sae-brasil-online.
Things that matter here:

* The query string is strict: exactly the documented parameters (`evt`;
  `id` and `display` for resultado.php), nothing else (an unknown
  parameter is a 400), and ids are sent as typed, case included.
* Errors look like {"success": false, "error": "...", "code": "not_found"};
  a wrong event id is 404 `not_found`.
* 120 requests a minute per client and endpoint; over it, 429 with
  `Retry-After`. Answers are cached for 5 s while the event runs and 300 s
  once it is finished (`Cache-Control: max-age`), so asking more often
  gains nothing.
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
# A resultado id: the event id, "_", 1 to 3 letters or digits ("26BR_MAN").
RESULTADO_ID = re.compile(r"^[A-Za-z0-9]{4}_[A-Za-z0-9]{1,3}$")
MAX_AGE = re.compile(r"(?:^|,)\s*max-age\s*=\s*(\d+)", re.IGNORECASE)


class ResultsApiError(Exception):
    """The results API could not answer. `code` is its error code, or ours."""

    def __init__(self, message: str, code: str, retry_after_s: int | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.retry_after_s = retry_after_s


@dataclass(frozen=True)
class ResultsTableAnswer:
    """resultado.php's answer as JSON, and how long the API says it stays fresh."""

    body: dict
    # Cache-Control max-age in seconds (5 while the event runs, 300 once it
    # is finished); None when the header is missing.
    max_age_s: int | None


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
        return self._get_answer(base_url, script, event_id).json()

    def _get_answer(
        self, base_url: str, script: str, event_id: str, extra: dict[str, str] | None = None
    ) -> httpx.Response:
        """The successful answer (its JSON already checked), or ResultsApiError."""
        if not EVENT_ID.match(event_id):
            raise ResultsApiError(
                f"{event_id!r} is not an event id (4 letters or digits)", "invalid_parameter"
            )
        url = f"{base_url.rstrip('/')}/resultados/{script}"
        try:
            response = self._http.get(url, params={"evt": event_id, **(extra or {})})
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
                what = f"resultado {extra['id']}" if extra and "id" in extra else "event"
                message = f"{what} not found in event {event_id} of the results API (case matters)"
                if what == "event":
                    message = f"event {event_id} not found in the results API (case matters)"
            raise ResultsApiError(message, code)
        try:
            response.json()
        except ValueError as error:
            raise ResultsApiError(
                "results API sent something that is not JSON", "bad_answer"
            ) from error
        return response

    def results_table(self, base_url: str, event_id: str, resultado_id: str) -> ResultsTableAnswer:
        """One results table with its display HTML (the penalties are only there)."""
        if not RESULTADO_ID.match(resultado_id):
            raise ResultsApiError(
                f"{resultado_id!r} is not a resultado id (event, '_', 1 to 3 letters or digits)",
                "invalid_parameter",
            )
        response = self._get_answer(
            base_url, "resultado.php", event_id, {"id": resultado_id, "display": "1"}
        )
        body = response.json()
        if not isinstance(body, dict):
            raise ResultsApiError("results API sent a table that is not an object", "bad_answer")
        found = MAX_AGE.search(response.headers.get("Cache-Control", ""))
        return ResultsTableAnswer(body=body, max_age_s=int(found.group(1)) if found else None)

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
