"""The Settings page's "Resultados das passagens" block, in words (LIVESTACK-47).

The configuration is a JSON text the admin edits; next to it, each
configured prova's table and how fetching it goes, and the "Testar"
answer. This module turns the orchestrator's answers into what the block
shows: plain functions over the API models, tested without a server.
Text from the results API (a cell's display HTML) is only ever passed on
as plain strings, so the templates escape it like any other text.
"""

import json
from dataclasses import dataclass, field
from datetime import datetime

from markupsafe import Markup

from review_ui.catalog import has_text, markup, text
from review_ui.formatting import recorded_time_text, relative_time_text
from review_ui.orchestrator_client import OrchestratorError
from review_ui.pass_result_view import fetch_warning, problem_sentence
from shared.models import (
    PassResultsProvaStatus,
    PassResultsSettings,
    PassResultTestRequest,
    PassResultTestResponse,
)


@dataclass(frozen=True)
class ProvaStatusRow:
    """One configured prova next to the configuration."""

    prova_id: str
    # Our prova with that API id, or "nenhuma prova sincronizada com esse id".
    prova_name: str
    synced: bool
    # "26BR_MAN", or None while no event is set.
    table: str | None
    # "buscando a cada 30 s" or why not.
    schedule: str
    # "última busca 14:02: ok" / "…: falhou" / "nunca buscada".
    last_fetch: str
    last_fetch_failed: bool
    # The orchestrator's English error for the last failed fetch.
    error: str | None
    # "inacessível desde 14:02", or None while it works.
    failing: str | None


def _schedule_key(prova: PassResultsProvaStatus) -> str:
    if prova.polling:
        return "pass_results.polling"
    if prova.fetch is not None and prova.fetch.event_finished:
        return "pass_results.event_finished"
    return "pass_results.not_polling"


def prova_status_row(prova: PassResultsProvaStatus, now: datetime) -> ProvaStatusRow:
    fetch = prova.fetch
    failed = fetch is not None and fetch.last_attempt_ok is False
    if fetch is None or fetch.last_attempt_at is None:
        last_fetch = text("pass_results.never_fetched")
    else:
        key = "pass_results.last_fetch.failed" if failed else "pass_results.last_fetch.ok"
        last_fetch = text(key, time=recorded_time_text(fetch.last_attempt_at, now))
    failing = None
    if fetch is not None and fetch.failing_since is not None:
        failing = text(
            "pass_results.failing_since", time=recorded_time_text(fetch.failing_since, now)
        )
    return ProvaStatusRow(
        prova_id=prova.prova_id,
        prova_name=prova.prova_name or text("pass_results.no_synced_prova"),
        synced=prova.prova_name is not None,
        table=prova.resultado_id,
        schedule=text(_schedule_key(prova)),
        last_fetch=last_fetch,
        last_fetch_failed=failed,
        error=fetch.last_error if failed and fetch is not None else None,
        failing=failing,
    )


def prova_ids_in(config_text: str) -> list[str] | None:
    """The prova ids of a configuration text, or None when it does not parse that far."""
    try:
        document = json.loads(config_text)
    except ValueError:
        return None
    provas = document.get("provas") if isinstance(document, dict) else None
    if not isinstance(provas, dict):
        return None
    return [key for key in provas if not key.startswith("_")]


def trial_prova_choices(typed_text: str, saved: PassResultsSettings | None) -> list[str]:
    """The "Testar" prova select: the ids in the text being edited, else the saved ones.

    Empty means a plain text field is shown instead (nothing to choose from).
    """
    typed = prova_ids_in(typed_text)
    if typed:
        return typed
    return [prova.prova_id for prova in saved.provas] if saved else []


# --- A configuration refused with 422 ---


def _problem_words(details: dict) -> str | Markup:
    """The problem code and its parameters in pt-BR ("o valor deve estar entre 0 e 3")."""
    problem = str(details.get("problem", ""))
    key = f"pass_results.config_problem.{problem}"
    if not has_text(key):
        return text("pass_results.config_problem.other")
    values = dict(details)
    if problem == "wrong_type":
        expected_key = f"pass_results.expected.{details.get('expected')}"
        values["expected"] = markup(expected_key) if has_text(expected_key) else "?"
    for name in ("allowed", "choices"):
        if isinstance(values.get(name), list):
            values[name] = ", ".join(str(item) for item in values[name])
    try:
        return markup(key, **values)
    except (KeyError, IndexError):
        # A parameter the orchestrator did not send: the sentence without it.
        return text("pass_results.config_problem.other")


def config_refusal(error: OrchestratorError, *, for_test: bool) -> Markup:
    """ "Configuração não salva: em <code>provas.MAN.result.decimals</code>, o valor …"

    Built from the 422's details {path, problem, ...}. The orchestrator's
    English message follows, marked as English, for a problem code this
    UI has no words for.
    """
    details = error.details or {}
    problem = _problem_words(details)
    path = details.get("path")
    reason = markup("pass_results.refused.at", path=path, problem=problem) if path else problem
    key = "pass_results.refused.test" if for_test else "pass_results.refused.save"
    sentence = markup(key, reason=reason)
    if not has_text(f"pass_results.config_problem.{details.get('problem')}"):
        sentence += Markup(' <span lang="en">{}</span>').format(error.message)
    return sentence


def too_long_refusal(max_length: int) -> Markup:
    """The same sentence when the text is too long to even send."""
    problem = markup("pass_results.config_problem.too_long", max_length=max_length)
    return markup("pass_results.refused.save", reason=problem)


# --- "Testar" ---


def cell_text(value: object) -> str:
    """A cell's raw value or display as plain text (a list of runs joined by " | ")."""
    if value is None:
        return "—"
    if isinstance(value, list):
        return " | ".join(cell_text(item) for item in value)
    return str(value)


@dataclass(frozen=True)
class TrialOutcome:
    """What the block shows after "Testar", without saving anything."""

    title: str
    # The line, or why there is none (+ the orchestrator's English details).
    text: str | None
    problem: str | None
    detail: str | None
    # "Tabela 26BR_MAN baixada há 5 s (14:02)." when it was fetched.
    fetched: Markup | None
    fetch_warning: str | None
    fetch_error: str | None
    unknown_penalties: str | None
    # "Cronômetro para em 66,0 s." or why not, for a prova with a clock
    # (LIVESTACK-48); None otherwise.
    clock: Markup | str | None = None
    # The car's rows: (column, raw, display) as plain strings, escaped by the template.
    rows: list[list[tuple[str, str, str]]] = field(default_factory=list)
    # True when the text tested is not the one saved.
    unsaved: bool = False


def trial_outcome(
    request: PassResultTestRequest,
    answer: PassResultTestResponse,
    now: datetime,
    *,
    unsaved: bool,
) -> TrialOutcome:
    fetch = answer.fetch
    fetched = None
    if fetch is not None and fetch.last_success_at is not None:
        fetched = markup(
            "pass_results.test.fetched",
            table=fetch.resultado_id,
            time=relative_time_text(fetch.last_success_at, now),
        )
    warning = fetch_warning(fetch, now)
    # A prova with only a clock has no line and nothing to say about one.
    clock_only = answer.clock_configured and answer.text is None and answer.problem is None
    return TrialOutcome(
        title=text(
            "pass_results.test.title",
            prova=request.prova_id,
            car=request.car_number,
            passagem=request.passagem,
        ),
        text=answer.text,
        problem=None if answer.text or clock_only else problem_sentence(answer.problem),
        detail=None if answer.text else answer.problem_detail,
        fetched=fetched,
        fetch_warning=warning,
        fetch_error=fetch.last_error if warning and fetch is not None else None,
        unknown_penalties=(
            text("pass_results.test.unknown_penalties", names=", ".join(answer.unknown_penalties))
            if answer.unknown_penalties
            else None
        ),
        clock=trial_clock(answer),
        rows=[
            [(cell_text(cell.column), cell_text(cell.raw), cell_text(cell.display)) for cell in row]
            for row in answer.cells
        ],
        unsaved=unsaved,
    )


def trial_clock(answer: PassResultTestResponse) -> Markup | str | None:
    """Where the running clock would stop (LIVESTACK-48), or why it would not run."""
    if not answer.clock_configured:
        return None
    if answer.clock_text is not None:
        return markup("pass_results.test.clock", text=answer.clock_text)
    return text("pass_results.test.clock_problem", reason=problem_sentence(answer.clock_problem))


# --- The whole block ---


@dataclass(frozen=True)
class PassResultsForm:
    """What the admin typed and what came back, when the block is shown after a POST.

    The default (nothing typed) shows the saved configuration.
    """

    # The textarea as typed (kept after a refusal or a test); None = the saved text.
    config_text: str | None = None
    # Why the configuration was refused, or why the test could not run.
    error: Markup | str | None = None
    # The "Testar" fields as typed.
    prova: str = ""
    car: str = ""
    passagem: str = ""
    outcome: TrialOutcome | None = None


@dataclass(frozen=True)
class PassResultsBlock:
    # False when the orchestrator could not be asked: the block says so.
    available: bool
    config_text: str
    rows: list[ProvaStatusRow]
    prova_choices: list[str]
    form: PassResultsForm


def pass_results_block(
    saved: PassResultsSettings | None, form: PassResultsForm, now: datetime
) -> PassResultsBlock:
    """The block for the page: the text in the box, the status rows, the test choices."""
    shown_text = form.config_text if form.config_text is not None else ""
    if form.config_text is None and saved is not None:
        shown_text = saved.config_text
    return PassResultsBlock(
        available=saved is not None,
        config_text=shown_text,
        rows=[prova_status_row(prova, now) for prova in saved.provas] if saved else [],
        prova_choices=trial_prova_choices(shown_text, saved),
        form=form,
    )
