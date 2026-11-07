"""A fake of the Baja results API, built from the example answers in its docs.

docs/results-api.md (repo tiagomartines11/baja-sae-brasil-online) shows
real answers from the platform's dev seed for event 26BR. The examples
below are copied from it (lists cut, as in the doc), and the fake behaves
the way the doc says the real API does:

* GET /resultados/evento.php and /resultados/equipes.php, each with
  exactly one parameter `evt`, and /resultados/resultado.php with `evt`,
  `id` and optionally `display=1` (anything else: 400 `unknown_parameter`,
  or `missing_parameter` / `invalid_parameter`);
* an unknown event or resultado (ids are case-sensitive: "26br" is not
  "26BR"): 404 `not_found`;
* `rate_limited_for_s` set: 429 with `Retry-After` and the doc's body;
* `unreachable` set: the connection fails, as when the API is down.

Results tables (`tables`, resultado id → the answer with display=1) are
empty by default; tests put the real answers of
tests/fixtures/results_api/ there (LIVESTACK-47). Without display=1 the
fake drops each cell's "display", as the real API does.

Tests change `events` to play a later state of an event (a renamed
prova, a team gone), and `tables` to play results entered or corrected.
"""

import copy
import re
from dataclasses import dataclass, field
from urllib.parse import parse_qsl

import httpx

FAKE_RESULTS_API_URL = "http://fake-results-api.test"

# GET /resultados/evento.php?evt=26BR (docs/results-api.md, cut as there).
EXAMPLE_EVENT_26BR = {
    "event": {
        "id": "26BR",
        "name": "31ª Competição Baja SAE BRASIL",
        "title": "Baja SAE BRASIL - 2026",
        "year": 2026,
        "type": "Nacional",
        "location": "em São José dos Campos - SP",
        "dates": "25 a 29 de Março de 2026",
        "listed": True,
    },
    "status": {"default_event": True, "finished": True, "spoilers_active": False},
    "resultados": [
        {"id": "26BR_END", "name": "Enduro", "group": "Enduro", "type": "table",
         "provas": ["END"], "url": "/resultados/resultado.php?evt=26BR&id=26BR_END"},
    ],
    "provas": [
        {"id": "SGE", "name": "Segurança Estática", "type": "normal", "status": "final",
         "tournament": False, "lap_counted": False, "live_url": None},
        {"id": "END", "name": "Enduro", "type": "laps", "status": "final",
         "tournament": False, "lap_counted": True,
         "live_url": "/resultados/corrida.php?evt=26BR&p=END"},
        {"id": "EPN", "name": "Penalizações Enduro", "type": "rolling", "status": "final",
         "tournament": False, "lap_counted": False, "live_url": None},
    ],
    "teams_url": "/resultados/equipes.php?evt=26BR",
}  # fmt: skip

# GET /resultados/equipes.php?evt=26BR (docs/results-api.md, cut as there).
EXAMPLE_TEAMS_26BR = {
    "event_id": "26BR",
    "teams": [
        {"number": 1, "name": "BAJA UFMG", "short_name": "Baja UFMG",
         "school": "Universidade Federal de Minas Gerais - UFMG", "school_short": "UFMG",
         "city": "Belo Horizonte", "state": "MG", "disqualified": False},
        {"number": 2, "name": "Equipe FEI Baja", "short_name": "FEI",
         "school": "Fundação Educacional Inaciana - FEI", "school_short": "FEI",
         "city": "São Bernardo do Campo", "state": "SP", "disqualified": False},
    ],
}  # fmt: skip

# The 429 body from the doc's "Rate limits" section.
RATE_LIMIT_BODY = {
    "error": "Too Many Requests",
    "message": "Limite de requisições excedido. Tente novamente em 12 segundos.",
    "retry_after": 12,
}

EVENT_ID = re.compile(r"^[A-Za-z0-9]{4}$")
RESULTADO_ID = re.compile(r"^[A-Za-z0-9]{4}_[A-Za-z0-9]{1,3}$")
SCRIPTS = {
    "/resultados/evento.php": "event",
    "/resultados/equipes.php": "teams",
    "/resultados/resultado.php": "table",
}
# The parameters each script accepts (docs/results-api.md, "Query string").
ACCEPTED = {"event": {"evt"}, "teams": {"evt"}, "table": {"evt", "id", "display"}}


def _error(status: int, code: str, message: str) -> httpx.Response:
    return httpx.Response(status, json={"success": False, "error": message, "code": code})


@dataclass
class FakeResultsApi:
    # event id → {"event": evento.php answer, "teams": equipes.php answer}
    events: dict[str, dict] = field(
        default_factory=lambda: {
            "26BR": {
                "event": copy.deepcopy(EXAMPLE_EVENT_26BR),
                "teams": copy.deepcopy(EXAMPLE_TEAMS_26BR),
            }
        }
    )
    # resultado id ("26BR_MAN") → resultado.php's answer with display=1.
    tables: dict[str, dict] = field(default_factory=dict)
    # Cache-Control max-age of results tables: 5 s while an event runs (the
    # default here), 300 s once it is finished.
    table_max_age_s: int = 5
    # Set to answer every request with 429 and this Retry-After.
    rate_limited_for_s: int | None = None
    # Set to fail every request at the connection, like an API that is down.
    unreachable: bool = False
    # Every (path, query string) received, for assertions.
    requests: list[tuple[str, str]] = field(default_factory=list)

    def handle(self, request: httpx.Request) -> httpx.Response:
        query = request.url.query.decode()
        self.requests.append((request.url.path, query))
        if self.unreachable:
            raise httpx.ConnectError("connection refused (fake results API is down)")
        if self.rate_limited_for_s is not None:
            seconds = self.rate_limited_for_s
            return httpx.Response(429, json=RATE_LIMIT_BODY, headers={"Retry-After": str(seconds)})
        kind = SCRIPTS.get(request.url.path)
        if kind is None or request.method != "GET":
            return _error(404, "not_found", "Não encontrado.")
        parameters = parse_qsl(query, keep_blank_values=True)
        names = [name for name, _ in parameters]
        unknown = [name for name in names if name not in ACCEPTED[kind]]
        if unknown:
            return _error(400, "unknown_parameter", f'Parâmetro desconhecido "{unknown[0]}".')
        if "evt" not in names:
            return _error(400, "missing_parameter", 'Falta o parâmetro "evt".')
        values = dict(parameters)
        if len(set(names)) != len(names) or not EVENT_ID.match(values["evt"]):
            return _error(400, "invalid_parameter", 'Parâmetro "evt" inválido.')
        event = self.events.get(values["evt"])  # case-sensitive, like the real one
        if event is None:
            return _error(404, "not_found", "Evento não encontrado.")
        if kind == "table":
            return self._table(values)
        return httpx.Response(200, json=event[kind])

    def _table(self, values: dict[str, str]) -> httpx.Response:
        if "id" not in values:
            return _error(400, "missing_parameter", 'Falta o parâmetro "id".')
        if not RESULTADO_ID.match(values["id"]) or values.get("display", "1") != "1":
            return _error(400, "invalid_parameter", "Parâmetro inválido.")
        table = self.tables.get(values["id"])
        if table is None or not values["id"].startswith(values["evt"] + "_"):
            return _error(404, "not_found", "Resultado não encontrado.")
        body = copy.deepcopy(table)
        if "display" not in values:
            for row in body.get("rows", []):
                for cell in row.get("cells", []):
                    cell.pop("display", None)
        headers = {"Cache-Control": f"public, max-age={self.table_max_age_s}"}
        return httpx.Response(200, json=body, headers=headers)

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    # --- Helpers for tests that play a later state of the event ---

    def provas_of(self, event_id: str = "26BR") -> list[dict]:
        return self.events[event_id]["event"]["provas"]

    def teams_of(self, event_id: str = "26BR") -> list[dict]:
        return self.events[event_id]["teams"]["teams"]

    def add_team(self, number: int, name: str, short_name: str | None = None, event_id="26BR"):
        self.teams_of(event_id).append(
            {"number": number, "name": name, "short_name": short_name, "school": None,
             "school_short": None, "city": None, "state": None, "disqualified": False}
        )  # fmt: skip
