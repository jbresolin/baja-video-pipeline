"""PUT /jobs/{id}/render-inputs: the rendering worker says what a clip was rendered from.

LIVESTACK-43 (docs/api.md §5 "Render inputs"). The worker sends it under
its process lease, after rendering and before the upload or the push, so
the report is on the job when the clip becomes a version (the outbox
upload's last chunk, or the studio's ACK of a direct push). If the version
already exists (a report that arrived late), it is updated in place.

A report never changes the job's state and never fails the task: a worker
that cannot send it delivers anyway, and the version then keeps the
orchestrator's own snapshot of the claim (domain/render_inputs.py).
"""

import sqlite3

from orchestrator.context import OrchestratorContext
from orchestrator.domain.job_view import require_job_row
from orchestrator.domain.leases import require_active_lease, sweep_expired_leases
from orchestrator.domain.render_inputs import inputs_json_for_clip
from orchestrator.errors import ApiError
from orchestrator.repositories import clips as clips_repo
from orchestrator.repositories import jobs as jobs_repo
from shared.models import ErrorCode, RenderInputsReport, TaskKind


def store_render_inputs_report(
    context: OrchestratorContext,
    worker_row: sqlite3.Row,
    job_id: str,
    lease_id: str,
    report: RenderInputsReport,
) -> None:
    with context.database.transaction() as connection:
        now = context.clock.now()
        sweep_expired_leases(connection, now)
        job_row = require_job_row(connection, job_id)
        require_active_lease(
            connection,
            lease_id,
            worker_id=worker_row["id"],
            job_id=job_id,
            kinds={TaskKind.PROCESS},
            now=now,
        )
        if report.raw_sha256 != job_row["sha256"]:
            raise ApiError(422, ErrorCode.HASH_MISMATCH, "raw_sha256 is not this job's raw", {})
        # Machine activity: no review version bump (repositories.jobs.update_job).
        jobs_repo.update_job(
            connection, job_id, now, {"reported_render_inputs_json": report.model_dump_json()}
        )
        if clips_repo.get_version_by_sha(connection, job_id, report.clip_sha256) is not None:
            refreshed = require_job_row(connection, job_id)
            inputs_json = inputs_json_for_clip(refreshed, report.clip_sha256)
            clips_repo.set_render_inputs(connection, job_id, report.clip_sha256, inputs_json)
