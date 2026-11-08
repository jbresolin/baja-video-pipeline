"""The official result of the passagem, in words (LIVESTACK-47).

The orchestrator sends `Job.pass_result`: whether the job's prova is
configured, whether the result will be burned in, the line itself ("66 s ·
4 cones → 86 s") or why there is none, and how fetching the results table
goes. This module turns that into what the job page shows under
"Mostrar resultado da passagem", and the fetch sentences the Settings
page shares with it. Plain functions over the API models, so they are
tested without a server.
"""

from dataclasses import dataclass
from datetime import datetime

from review_ui.catalog import text
from review_ui.formatting import recorded_time_text
from shared.models import PassResult, PassResultFetch, PassResultProblem

# One sentence per reason the job has no result line; a reason this UI does
# not know yet fails at import (built from the catalog for every value).
PROBLEM_SENTENCES: dict[PassResultProblem, str] = {
    problem: text(f"pass_result.problem.{problem.value}") for problem in PassResultProblem
}


@dataclass(frozen=True)
class PassResultBox:
    """What the job page shows for the result of the passagem."""

    # The checkbox's state: the toggle in force (the reviewer's, else the prova's default).
    show: bool
    # "Na tarja: 66 s · 4 cones → 86 s", "Não será gravado (resultado: …).", or why none.
    status: str
    # The orchestrator's English details of the problem ("'DNF'"), or None.
    detail: str | None
    # "API de resultados inacessível desde 14:02 (último resultado de 13:58).", or None.
    fetch_warning: str | None
    # The orchestrator's English error for the last failed fetch, or None.
    fetch_error: str | None
    # There is a line to burn in (shown or not): with a running clock it
    # appears when the clock stops, so the clock block warns when the cut
    # ends before that (LIVESTACK-53, js/clock_late.js).
    has_text: bool = False


def problem_sentence(problem: PassResultProblem | None) -> str:
    """Why there is no result line. No problem but no text either reads as "not yet"."""
    return PROBLEM_SENTENCES[problem or PassResultProblem.NO_RESULT_YET]


def fetch_warning(fetch: PassResultFetch | None, now: datetime) -> str | None:
    """A sentence when the results API is failing, or None while it works.

    Times are São Paulo time: "14:02" on the same day as `now`, else with the date.
    """
    if fetch is None or not (fetch.stale or fetch.last_attempt_ok is False):
        return None
    since = fetch.failing_since or fetch.last_attempt_at
    since_text = recorded_time_text(since, now)
    if fetch.last_success_at is None:
        return text("pass_result.failing.never_fetched", since=since_text)
    return text(
        "pass_result.failing", since=since_text, last=recorded_time_text(fetch.last_success_at, now)
    )


def pass_result_box(result: PassResult | None, now: datetime) -> PassResultBox | None:
    """The job page's result block, or None when the prova shows no result at all.

    None for a prova without an entry in the configuration (no toggle is
    offered then) and for an orchestrator too old to send `pass_result`.
    """
    if result is None or not result.configured:
        return None
    detail = None
    if result.text is not None and result.show:
        status = text("pass_result.on_air", text=result.text)
    elif result.text is not None:
        status = text("pass_result.not_burned", text=result.text)
    else:
        status = problem_sentence(result.problem)
        detail = result.problem_detail
    fetch = result.fetch
    warning = fetch_warning(fetch, now)
    return PassResultBox(
        show=result.show,
        status=status,
        detail=detail,
        fetch_warning=warning,
        fetch_error=fetch.last_error if warning and fetch is not None else None,
        has_text=result.text is not None,
    )
