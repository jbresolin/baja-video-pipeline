"""Official pass results in the overlay (LIVESTACK-47): what the job page and Settings read.

The orchestrator fetches each configured prova's results table from the
Baja results API (`resultado.php?…&display=1`), keeps the last good
answer, and builds one line of text per passagem ("66 s · 4 cones →
86 s"). Which provas, which columns and how penalties are named is one
JSON document edited on the admin Settings page (docs/design.md §32).
"""

from datetime import datetime
from typing import Any

from pydantic import Field

from shared.models.common import CarNumber, PassNumber, ReadByClients, ReceivedByServer
from shared.models.enums import PassResultProblem

# The configuration is a small JSON document typed by hand; this is far
# more than a whole event needs.
MAX_PASS_RESULTS_CONFIG_LENGTH = 20_000


class PassResultFetch(ReadByClients):
    """How fetching one results table has gone, e.g. "26BR_MAN".

    A failed fetch keeps the last good answer: texts keep coming from it
    (`stale` says so) and nothing on air is blanked by a failure.

    Read by clients: unknown fields are ignored.
    """

    # The results API's resultado id: the event id, "_", the configured id.
    resultado_id: str
    # The last request, whether it worked, and its error when it did not.
    last_attempt_at: datetime | None = None
    last_attempt_ok: bool | None = None
    last_error: str | None = None
    # When the answer the texts come from was fetched; None = never.
    last_success_at: datetime | None = None
    # The first failure after the last success (None while it works):
    # "API inacessível desde 14:02".
    failing_since: datetime | None = None
    # True when the last request failed but an older answer is in use.
    stale: bool = False
    # The API said the event is finished and the last fetch after that was
    # done: the table is no longer fetched (only "Testar" still asks).
    event_finished: bool = False


class PassResult(ReadByClients):
    """The official result of a job's passagem, as it would be burned in now.

    Read by clients: unknown fields are ignored.
    """

    # The job's prova has an entry in the configuration: the job page then
    # offers "Mostrar resultado da passagem". False = no result line at all.
    configured: bool = False
    # The toggle as it applies: Job.show_pass_result, else the prova's
    # `show_by_default`. Always False when not configured.
    show: bool = False
    # The line from the latest results fetched, whether or not `show` is on
    # (the job page shows what ticking the box would burn in). None = no
    # result to show, and `problem` says why.
    text: str | None = None
    problem: PassResultProblem | None = None
    # Details for the problem, in English (the column name, the value the
    # API sent such as "DNF", the number of runs found).
    problem_detail: str | None = None
    # The fetch status of the table the text comes from; None when the job
    # has no table to read (not configured, no event).
    fetch: PassResultFetch | None = None


class PassResultsProvaStatus(ReadByClients):
    """One configured prova on the Settings page: its table and how fetching it goes.

    Read by clients: unknown fields are ignored.
    """

    # The key in the configuration: the results API's prova id ("MAN").
    prova_id: str
    # Our prova with that API id (synced from the results API), or None
    # when no prova of ours has it (a typo, or the provas were not synced).
    prova_name: str | None = None
    # "26BR_MAN"; None while no event id is set.
    resultado_id: str | None = None
    # True when the orchestrator fetches it: jobs of the prova need results
    # (in review, ready, rendering or delivered) and the event is not
    # finished (fetch.event_finished says when that stopped it).
    polling: bool = False
    fetch: PassResultFetch | None = None


class PassResultsSettings(ReadByClients):
    """GET /admin/pass-results: the configuration as saved, and each prova's fetch status.

    Read by clients: unknown fields are ignored.
    """

    # Exactly as the admin saved it; "" = nothing configured.
    config_text: str
    provas: list[PassResultsProvaStatus] = Field(default_factory=list)


class UpdatePassResultsConfigRequest(ReceivedByServer):
    """PUT /admin/pass-results/config: replace the configuration.

    Validated before it is stored; a bad one answers 422 validation_error
    with `details` {path, problem, ...} (docs/api.md) and the old one stays
    in force. "" turns pass results off.

    Received by the orchestrator: unknown fields are refused.
    """

    config_text: str = Field(max_length=MAX_PASS_RESULTS_CONFIG_LENGTH)


class PassResultTestRequest(ReceivedByServer):
    """POST /admin/pass-results/test: the "Testar" button.

    Fetches the prova's table now (unless fetched in the last few seconds)
    and builds the text for one car and passagem. `config_text`, when sent,
    is used instead of the saved configuration, so a change can be tried
    before saving; it is validated the same way.

    Received by the orchestrator: unknown fields are refused.
    """

    prova_id: str = Field(min_length=1, max_length=3)
    car_number: CarNumber
    passagem: PassNumber
    config_text: str | None = Field(default=None, max_length=MAX_PASS_RESULTS_CONFIG_LENGTH)


class PassResultCell(ReadByClients):
    """One cell of the car's row as the API sent it, for "Testar".

    Read by clients: unknown fields are ignored.
    """

    column: str | None
    raw: Any = None
    display: Any = None


class PassResultTestResponse(ReadByClients):
    """What "Testar" shows: the text (or why none), and what it was built from.

    Read by clients: unknown fields are ignored.
    """

    resultado_id: str | None = None
    text: str | None = None
    problem: PassResultProblem | None = None
    problem_detail: str | None = None
    fetch: PassResultFetch | None = None
    # The car's row (each row for a prova with one row per run), so the
    # admin can see what the text was built from.
    cells: list[list[PassResultCell]] = Field(default_factory=list)
    # Penalty icons in the table that the configuration has no name for;
    # they are shown with their class name and logged.
    unknown_penalties: list[str] = Field(default_factory=list)
    # The running clock (LIVESTACK-48), for a prova with a `clock` block:
    # the official time it freezes on, as the clock writes it ("66,0"), or
    # why it is not known. A prova with only a clock has no `text`.
    clock_configured: bool = False
    clock_text: str | None = None
    # Its unit, written after it ("66,0 s"; LIVESTACK-53).
    clock_unit: str | None = None
    clock_problem: PassResultProblem | None = None
    clock_problem_detail: str | None = None
