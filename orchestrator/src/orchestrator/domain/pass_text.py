"""The overlay's result line for one passagem (LIVESTACK-47).

The one function that turns the results API's table, the prova's
configuration and the passagem into the text burned into the clip. The
render task, the job page, the "clip no longer matches" check and the
Settings page's "Testar" all ask build_pass_text(), so they always agree.

    car 26, passagem 2 of the 2026 Manobrabilidade → "66 s · 4 cones → 86 s"
    car 2, passagem 1                              → "58 s · 8 cones, 2 saídas de pista → 118 s"
    car 26, passagem 1 (a clean run)               → "59 s"
    car 1, passagem 2 of a Suspensão with an extra → "37,81 s · 8 obstáculos"

The time, then its penalties by their configured names, then "→" and the
corrected value when it differs from the time (as shown, i.e. after
rounding to the configured decimals), then each `extra` column's value
for the same run. An extra with no value for the run is left out; the
time alone is still the result. Numbers are written the Brazilian way
(decimal comma), with exactly the configured decimals.
"""

import logging
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from orchestrator.domain.pass_results_config import PenaltyNames, ProvaPassConfig
from orchestrator.domain.results_table import (
    ResultsTable,
    RunNumber,
    find_run,
    penalties_in,
    run_number,
)
from shared.models import PassResultProblem

logger = logging.getLogger(__name__)

# The longest line a task carries (shared.models.common.PassResultText).
# The overlay shrinks and cuts long lines anyway; this only keeps a
# configuration with many long penalty names from breaking the task.
MAX_TEXT_LENGTH = 300
ELLIPSIS = "…"
PART_SEPARATOR = " · "
PENALTY_SEPARATOR = ", "
CORRECTED_ARROW = " → "

# Penalty kinds already logged as unknown, so a busy job page does not log
# the same warning on every view.
_logged_unknown_kinds: set[str] = set()


@dataclass(frozen=True)
class PassText:
    """The line, or why there is none."""

    text: str | None = None
    problem: PassResultProblem | None = None
    detail: str | None = None
    # Icon classes the configuration has no names for (shown by class name).
    unknown_penalties: tuple[str, ...] = ()


def format_number(value: Decimal, decimals: int) -> str:
    """66 → "66"; 59.44 with 1 decimal → "59,4"; 59.45 → "59,5" (half up, as people round)."""
    step = Decimal(1).scaleb(-decimals)
    return str(value.quantize(step, rounding=ROUND_HALF_UP)).replace(".", ",")


def with_unit(number_text: str, unit: str) -> str:
    return f"{number_text} {unit}" if unit else number_text


def run_value(
    table: ResultsTable, config: ProvaPassConfig, column: str, car_number: int, passagem: int
) -> RunNumber:
    """One column's value for the car's passagem, as a number (or why there is none).

    Also what the running clock reads for its official time (LIVESTACK-48).
    """
    lookup = find_run(table, config.runs, column, car_number, passagem)
    if lookup.cell is None:
        return RunNumber(problem=lookup.problem, detail=lookup.detail)
    return run_number(lookup.cell.raw)


def build_pass_text(
    table: ResultsTable,
    config: ProvaPassConfig,
    penalty_names: dict[str, PenaltyNames],
    car_number: int,
    passagem: int,
) -> PassText:
    spec = config.result
    if spec is None:
        return PassText(problem=PassResultProblem.NOT_CONFIGURED, detail="no 'result' block")
    lookup = find_run(table, config.runs, spec.column, car_number, passagem)
    if lookup.cell is None:
        return PassText(problem=lookup.problem, detail=lookup.detail)
    time = run_number(lookup.cell.raw)
    if time.value is None:
        return PassText(problem=time.problem, detail=time.detail)
    shown_time = format_number(time.value, spec.decimals)
    text = with_unit(shown_time, spec.unit)

    unknown: list[str] = []
    if spec.penalties:
        words = []
        for kind, count in penalties_in(lookup.cell.display):
            names = penalty_names.get(kind)
            if names is None:
                unknown.append(kind)
                _log_unknown_kind(kind, config.prova_id)
                words.append(f"{count} {kind}")
            else:
                words.append(f"{count} {names.for_count(count)}")
        if words:
            text += PART_SEPARATOR + PENALTY_SEPARATOR.join(words)

    if spec.corrected_column:
        corrected = run_value(table, config, spec.corrected_column, car_number, passagem)
        # No corrected value yet: the time alone is still the official time.
        if corrected.value is not None:
            shown_corrected = format_number(corrected.value, spec.decimals)
            if shown_corrected != shown_time:
                text += CORRECTED_ARROW + with_unit(shown_corrected, spec.unit)
    for extra in spec.extra:
        value = run_value(table, config, extra.column, car_number, passagem)
        if value.value is not None:
            text += PART_SEPARATOR + with_unit(
                format_number(value.value, extra.decimals), extra.unit
            )
    if len(text) > MAX_TEXT_LENGTH:
        text = text[: MAX_TEXT_LENGTH - 1].rstrip() + ELLIPSIS
    return PassText(text=text, unknown_penalties=tuple(unknown))


def _log_unknown_kind(kind: str, prova_id: str) -> None:
    if kind not in _logged_unknown_kinds:
        _logged_unknown_kinds.add(kind)
        logger.warning(
            "results API penalty icon %r (prova %s) has no name in the pass-results"
            " configuration; shown by its class name",
            kind,
            prova_id,
        )
