"""A job's running clock, from the largada and the official time (LIVESTACK-48).

    the prova's `clock` block (Settings) ─┐
    the job's largada and toggle          ─┼─► pass_clock_for_job ─► Job.pass_clock
    the passagem's official time          ─┘          │
      (the `clock.column` value, from the            └─► .render: what a process task draws
       last good answer of the results table)            (ProcessTask.pass_clock)

Like the result line (pass_results.py), the job page, the render task, the
render features it needs and the "clip no longer matches" check all come
through here, so they see the same clock.

The official time is the run's raw time, never the corrected one (that
is what the clock column names); it is rounded down to the clock's
decimals once, here, into whole clock units (shared.pass_clock).
"""

import sqlite3
from dataclasses import dataclass
from typing import Any

from orchestrator.domain.pass_results import (
    api_prova_id,
    current_config,
    event_id,
    fetch_status,
    resultado_id_for,
    stored_table,
)
from orchestrator.domain.pass_results_config import ProvaPassConfig
from orchestrator.domain.pass_text import run_value
from orchestrator.domain.results_table import ResultsTable
from orchestrator.repositories import pass_results as pass_results_repo
from shared.models import PassClock, PassResultProblem, RenderClock
from shared.pass_clock import clock_text, official_units


@dataclass(frozen=True)
class OfficialClock:
    """Where the clock freezes for a passagem, or why it is not known."""

    units: int | None = None
    text: str | None = None
    problem: PassResultProblem | None = None
    detail: str | None = None


def official_clock(
    table: ResultsTable, prova_config: ProvaPassConfig, car_number: int, passagem: int
) -> OfficialClock:
    """The `clock.column` value of the car's passagem, in clock units and as the clock shows it.

    The same value "Testar" shows on the Settings page and a render draws.
    """
    spec = prova_config.clock
    if spec is None:
        return OfficialClock(problem=PassResultProblem.NOT_CONFIGURED, detail="no 'clock' block")
    official = run_value(table, prova_config, spec.column, car_number, passagem)
    if official.value is None:
        return OfficialClock(problem=official.problem, detail=official.detail)
    if official.value < 0:
        # Never a time; a clock cannot count up to it.
        return OfficialClock(problem=PassResultProblem.NOT_A_NUMBER, detail="negative")
    units = official_units(official.value, spec.decimals)
    return OfficialClock(units=units, text=clock_text(units, spec.decimals))


def pass_clock_for_job(connection: sqlite3.Connection, job_row: Any) -> PassClock:
    """Job.pass_clock: the toggle, the largada, the official time, and what a render draws.

    `job_row` is a jobs row, or a dict of its columns (a reprocess checks
    the fields it is about to save before they are stored).
    """
    config = current_config(connection)
    prova_config = config.for_prova(api_prova_id(connection, job_row["prova"]))
    if prova_config is None or prova_config.clock is None:
        return PassClock(problem=PassResultProblem.NOT_CONFIGURED)
    spec = prova_config.clock
    chosen = job_row["show_clock"]
    show = prova_config.show_by_default if chosen is None else bool(chosen)
    largada = job_row["largada_s"]
    base = PassClock(
        configured=True, show=show, largada_s=largada, decimals=spec.decimals, unit=spec.unit
    )

    event = event_id(connection)
    if not event:
        return base.model_copy(update={"problem": PassResultProblem.NO_EVENT})
    if job_row["car_number"] is None or job_row["passagem"] is None:
        return base.model_copy(update={"problem": PassResultProblem.INCOMPLETE_REVIEW})
    resultado_id = resultado_id_for(event, prova_config)
    table_row = pass_results_repo.get_table_row(connection, resultado_id)
    # A prova may have a clock and no result line: the job page then shows
    # the fetch status from here.
    base = base.model_copy(update={"fetch": fetch_status(table_row, resultado_id)})
    table = stored_table(table_row)
    if table is None:
        return base.model_copy(update={"problem": PassResultProblem.NOT_FETCHED})
    official = official_clock(table, prova_config, job_row["car_number"], job_row["passagem"])
    if official.units is None:
        return base.model_copy(
            update={"problem": official.problem, "problem_detail": official.detail}
        )
    render = None
    if show and largada is not None:
        render = RenderClock(
            largada_s=largada,
            official_units=official.units,
            decimals=spec.decimals,
            unit=spec.unit,
        )
    return base.model_copy(
        update={"official_units": official.units, "official_text": official.text, "render": render}
    )


def clock_for_render(connection: sqlite3.Connection, job_row: Any) -> RenderClock | None:
    """ProcessTask.pass_clock for this job now (None = no clock in the clip)."""
    return pass_clock_for_job(connection, job_row).render
