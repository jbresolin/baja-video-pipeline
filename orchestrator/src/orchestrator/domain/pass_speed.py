"""A job's top speed under the clock, from the speed mark and the speed table (LIVESTACK-67).

    the prova's `speed` block (Settings) ─┐
    the job's speed mark                  ─┼─► pass_speed_for_job ─► Job.pass_speed
    the run's official speed              ─┤          │
      (the `speed.column` value, from the  │          └─► .render: what a process task draws
       last good answer of the SPEED table)│              (ProcessTask.pass_speed)
    the clock a render would draw         ─┘

"Aceleração e Velocidade" has two results tables for one run: the clock's
(ACE, the prova's own `resultado`) and the speed's (VEL, `speed.resultado`).
Run n of the speed column pairs with run n of the clock column, read the
same way (the prova's `runs`), so pass_text.run_value reads both.

Like the clock (pass_clock.py), the job page, the render task, the render
features, the result hold and the "clip no longer matches" check all come
through here, so they see the same speed.

The speed is drawn only in a clip that has a clock (design §40): it sits
under the clock, and one corner has one layout. Without a clock (no
largada, "Mostrar cronômetro" off, no official time) there is no speed
pill either, and the job page says so.
"""

import sqlite3
from dataclasses import dataclass
from typing import Any

from orchestrator.domain.pass_clock import pass_clock_for_job
from orchestrator.domain.pass_results import (
    api_prova_id,
    current_config,
    event_id,
    fetch_status,
    stored_table,
)
from orchestrator.domain.pass_results_config import ProvaPassConfig
from orchestrator.domain.pass_text import format_number, run_value
from orchestrator.domain.results_table import ResultsTable
from orchestrator.repositories import pass_results as pass_results_repo
from shared.models import PassClock, PassResultProblem, PassSpeed, RenderSpeed


@dataclass(frozen=True)
class OfficialSpeed:
    """The run's official speed as the pill writes it ("37,2"), or why it is not known."""

    text: str | None = None
    problem: PassResultProblem | None = None
    detail: str | None = None


def speed_resultado_id_for(event: str, prova_config: ProvaPassConfig) -> str | None:
    """ "26BR" + speed.resultado "VEL" → "26BR_VEL"; None for a prova without a speed."""
    if prova_config.speed is None:
        return None
    return f"{event}_{prova_config.speed.resultado}"


def official_speed(
    table: ResultsTable, prova_config: ProvaPassConfig, car_number: int, passagem: int
) -> OfficialSpeed:
    """The `speed.column` value of the car's passagem in the speed table, as written.

    Rounded half up to the configured decimals with a decimal comma, like
    the result line's numbers (37.18 km/h with 1 decimal → "37,2"). The same
    value "Testar" shows on the Settings page and a render draws.
    """
    spec = prova_config.speed
    if spec is None:
        return OfficialSpeed(problem=PassResultProblem.NOT_CONFIGURED, detail="no 'speed' block")
    speed = run_value(table, prova_config, spec.column, car_number, passagem)
    if speed.value is None:
        return OfficialSpeed(problem=speed.problem, detail=speed.detail)
    if speed.value < 0:
        # Never a speed through the trap.
        return OfficialSpeed(problem=PassResultProblem.NOT_A_NUMBER, detail="negative")
    return OfficialSpeed(text=format_number(speed.value, spec.decimals))


def pass_speed_for_job(
    connection: sqlite3.Connection, job_row: Any, clock: PassClock | None = None
) -> PassSpeed:
    """Job.pass_speed: the mark, the official speed, and what a render draws.

    `job_row` is a jobs row, or a dict of its columns (the pass preview puts
    unsaved values in). `clock` is Job.pass_clock for the same job when the
    caller has it already; otherwise it is worked out here.
    """
    config = current_config(connection)
    prova_config = config.for_prova(api_prova_id(connection, job_row["prova"]))
    if prova_config is None or prova_config.speed is None:
        return PassSpeed(problem=PassResultProblem.NOT_CONFIGURED)
    spec = prova_config.speed
    mark = job_row["speed_mark_s"]
    base = PassSpeed(configured=True, speed_mark_s=mark, decimals=spec.decimals, unit=spec.unit)

    event = event_id(connection)
    if not event:
        return base.model_copy(update={"problem": PassResultProblem.NO_EVENT})
    if job_row["car_number"] is None or job_row["passagem"] is None:
        return base.model_copy(update={"problem": PassResultProblem.INCOMPLETE_REVIEW})
    resultado_id = speed_resultado_id_for(event, prova_config)
    table_row = pass_results_repo.get_table_row(connection, resultado_id)
    base = base.model_copy(update={"fetch": fetch_status(table_row, resultado_id)})
    table = stored_table(table_row)
    if table is None:
        return base.model_copy(update={"problem": PassResultProblem.NOT_FETCHED})
    official = official_speed(table, prova_config, job_row["car_number"], job_row["passagem"])
    if official.text is None:
        return base.model_copy(
            update={"problem": official.problem, "problem_detail": official.detail}
        )
    if clock is None:
        clock = pass_clock_for_job(connection, job_row)
    render = None
    # Drawn only under a clock (design §40), and only once marked.
    if clock.render is not None and mark is not None:
        render = RenderSpeed(speed_mark_s=mark, text=official.text, unit=spec.unit)
    return base.model_copy(update={"official_text": official.text, "render": render})


def speed_for_render(connection: sqlite3.Connection, job_row: Any) -> RenderSpeed | None:
    """ProcessTask.pass_speed for this job now (None = no speed pill in the clip)."""
    return pass_speed_for_job(connection, job_row).render
