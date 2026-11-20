"""The result line and the clock for prova, car and passagem not saved yet (LIVESTACK-53).

On a new video the reviewer chooses the prova and types car and passagem;
"Resultado da passagem" and "Cronômetro" used to appear only after a save
and a reload, because the page built them from the saved job. The page
now asks here while the reviewer types, and redraws both blocks in place.

The answer is computed by the very functions that make Job.pass_result
and Job.pass_clock, on the saved job with the asked values put in, so it
is exactly what the job would show after saving them. Nothing is stored.
A value left out keeps the saved one, as PATCH …/review does with a field
left out.

The first video of a prova finds its table never fetched (the background
loop only fetches tables of provas with saved jobs). The preview then
fetches it once, as "Testar" does: at most 3 s, not again within 30 s,
and "ainda não buscado" when it fails (pass_results_fetch). A prova with a
speed (LIVESTACK-67, "Aceleração e Velocidade") has a second table, the
speed's, fetched the same way, so choosing it shows the clock block with
both marks and the official speed without a save.
"""

from orchestrator.context import OrchestratorContext
from orchestrator.domain.job_view import require_job_row
from orchestrator.domain.pass_clock import pass_clock_for_job
from orchestrator.domain.pass_results import (
    api_prova_id,
    current_config,
    event_id,
    pass_result_for_job,
    resultado_id_for,
)
from orchestrator.domain.pass_results_fetch import fetch_if_never_fetched
from orchestrator.domain.pass_speed import pass_speed_for_job, speed_resultado_id_for
from shared.models import PassPreview


def pass_preview(
    context: OrchestratorContext,
    job_id: str,
    *,
    prova: str | None,
    car_number: int | None,
    passagem: int | None,
) -> PassPreview:
    """GET /jobs/{id}/pass-preview: Job.pass_result and Job.pass_clock for these values."""
    asked = {"prova": prova, "car_number": car_number, "passagem": passagem}
    with context.database.transaction() as connection:
        # A dict of the job's columns: both functions read a row or a dict.
        columns = dict(require_job_row(connection, job_id))
        columns.update({name: value for name, value in asked.items() if value is not None})
        resultado_ids = _configured_tables(connection, columns["prova"])
    for resultado_id in resultado_ids:
        # Outside any transaction: it may wait for the API.
        fetch_if_never_fetched(context, resultado_id)
    with context.database.transaction() as connection:
        clock = pass_clock_for_job(connection, columns)
        return PassPreview(
            pass_result=pass_result_for_job(connection, columns),
            pass_clock=clock,
            pass_speed=pass_speed_for_job(connection, columns, clock),
        )


def _configured_tables(connection, prova_name: str | None) -> list[str]:
    """The resultado ids of the prova's tables: its own, then its speed's when it has one."""
    prova_config = current_config(connection).for_prova(api_prova_id(connection, prova_name))
    event = event_id(connection)
    if prova_config is None or not event:
        return []
    tables = [resultado_id_for(event, prova_config)]
    speed_table = speed_resultado_id_for(event, prova_config)
    if speed_table is not None:
        tables.append(speed_table)
    return tables
