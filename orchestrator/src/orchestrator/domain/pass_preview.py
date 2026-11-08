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
"""

from orchestrator.context import OrchestratorContext
from orchestrator.domain.job_view import require_job_row
from orchestrator.domain.pass_clock import pass_clock_for_job
from orchestrator.domain.pass_results import pass_result_for_job
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
        return PassPreview(
            pass_result=pass_result_for_job(connection, columns),
            pass_clock=pass_clock_for_job(connection, columns),
        )
