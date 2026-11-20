""" "Testar" on the Settings page's "Resultados das passagens" block, in words (LIVESTACK-47).

The admin picks a prova, a car and a passagem; the orchestrator fetches the
tables and answers the line a render would burn in (or why none), the
clock's stop (LIVESTACK-48), the top speed under it (LIVESTACK-67), and
the car's row as the API sent it. This module turns that answer into what
the block shows. Moved out of pass_results_settings_view.py when the speed
made that file too long. Plain functions over the API models, tested
without a server. Text from the results API is only passed on as plain
strings, so the templates escape it like any other text.
"""

from dataclasses import dataclass, field
from datetime import datetime

from markupsafe import Markup

from review_ui.catalog import markup, text
from review_ui.formatting import relative_time_text, short_number
from review_ui.pass_result_view import fetch_warning, problem_sentence
from shared.models import PassResultTestRequest, PassResultTestResponse
from shared.pass_clock import with_unit


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
    # "Velocidade: 37,2 km/h." or why not, for a prova with a speed under
    # its clock (LIVESTACK-67); None otherwise.
    speed: Markup | str | None = None
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
        speed=trial_speed(answer),
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
        return markup(
            "pass_results.test.clock", text=with_unit(answer.clock_text, answer.clock_unit)
        )
    return text("pass_results.test.clock_problem", reason=problem_sentence(answer.clock_problem))


def trial_speed(answer: PassResultTestResponse) -> Markup | str | None:
    """The top speed under the clock (LIVESTACK-67): "Velocidade: 37,2 km/h.", or why none.

    For a prova that shows it N s before the clip's end (LIVESTACK-68):
    "Velocidade: 37,2 km/h (entra 3 s antes do fim do clipe)."
    """
    if not answer.speed_configured:
        return None
    if answer.speed_text is not None:
        # "" when the speed has no unit (None would read as the clock's "s").
        shown = with_unit(answer.speed_text, answer.speed_unit or "")
        if answer.speed_seconds_before_end is not None:
            seconds = short_number(answer.speed_seconds_before_end)
            return markup("pass_results.test.speed_before_end", text=shown, seconds=seconds)
        return markup("pass_results.test.speed", text=shown)
    return text("pass_results.test.speed_problem", reason=problem_sentence(answer.speed_problem))
