"""PUT /jobs/{id}/media-info: the raw's ffprobe report, stored on the job.

The first holder sends it while it holds the make_proxy lease, before the
thumbnail and proxy (docs/api.md §7.1). The review UI shows it under the
player. Location tags are stripped again here, whatever the worker did, so
GPS coordinates can never end up in the jobs DB.
"""

import json
import sqlite3

from pydantic import ValidationError

from orchestrator.context import OrchestratorContext
from orchestrator.db.database import from_db_time
from orchestrator.domain.job_view import require_job_row
from orchestrator.domain.leases import require_active_lease, sweep_expired_leases
from orchestrator.domain.recording_time import recorded_at_columns
from orchestrator.domain.vertical_reply import PendingVerticalReply, vertical_reply_for
from orchestrator.errors import ApiError
from orchestrator.repositories import jobs as jobs_repo
from shared.framing import display_size_from_probe
from shared.media_details import strip_location_tags
from shared.media_probe import parse_frame_rate
from shared.models import ErrorCode, ProbeReport, TaskKind

# An ffprobe report is a few KB; anything this large is not one.
MAX_MEDIA_INFO_BYTES = 512_000


def check_media_info_size(body: bytes) -> None:
    if len(body) > MAX_MEDIA_INFO_BYTES:
        raise ApiError(413, ErrorCode.UPLOAD_TOO_LARGE, "media info is over 512 KB")


def parse_media_info(body: bytes) -> ProbeReport:
    """The request body as a ProbeReport, or 400 validation_error."""
    try:
        return ProbeReport.model_validate_json(body)
    except ValidationError as error:
        problems = [
            {"loc": list(problem.get("loc", [])), "msg": problem.get("msg", "")}
            for problem in error.errors()
        ]
        raise ApiError(
            400, ErrorCode.VALIDATION_ERROR, "body is not a ProbeReport", {"errors": problems}
        ) from error


def _first_video_frame_rate(report: ProbeReport) -> float | None:
    video = next((s for s in report.streams if s.get("codec_type") == "video"), {})
    return parse_frame_rate(video.get("avg_frame_rate")) or parse_frame_rate(
        video.get("r_frame_rate")
    )


def store_media_info(
    context: OrchestratorContext,
    worker_row: sqlite3.Row,
    job_id: str,
    lease_id: str,
    report: ProbeReport,
) -> PendingVerticalReply | None:
    """Store the report; returns the bot's vertical-video reply to send afterwards, if any."""
    cleaned = strip_location_tags(report)
    with context.database.transaction() as connection:
        now = context.clock.now()
        sweep_expired_leases(connection, now)
        job_row = require_job_row(connection, job_id)
        require_active_lease(
            connection,
            lease_id,
            worker_id=worker_row["id"],
            job_id=job_id,
            kinds={TaskKind.MAKE_PROXY},
            now=now,
        )
        changes: dict = {"media_info_json": json.dumps(cleaned.model_dump(mode="json"))}
        # Job.frame_rate comes from the same probe until the proxy upload's
        # Frame-Rate header (the proxy's exact rate) replaces it.
        if job_row["frame_rate"] is None:
            frame_rate = _first_video_frame_rate(cleaned)
            if frame_rate is not None:
                changes["frame_rate"] = frame_rate
        # When it was filmed: the raw's creation_time, when plausible (LIVESTACK-45).
        changes.update(recorded_at_columns_for(job_row, cleaned))
        # The picture as players show it, after the rotation note (LIVESTACK-50).
        size = display_size_from_probe(cleaned.streams)
        changes.update(display_size_columns(size))
        # Decided before the update: only the first report of a video may ask.
        vertical_reply = vertical_reply_for(context, job_row, size)
        # Machine activity: no version bump (repositories.jobs.update_job).
        jobs_repo.update_job(connection, job_id, now, changes)
    return vertical_reply


def display_size_columns(size: tuple[int, int] | None) -> dict[str, int | None]:
    """display_width/height for a size from shared.framing.display_size_from_probe."""
    width, height = size if size is not None else (None, None)
    return {"display_width": width, "display_height": height}


def recorded_at_columns_for(job_row: sqlite3.Row, report: ProbeReport | None) -> dict[str, str]:
    """recorded_at and its source for this job, given its probe report (or none)."""
    received_at = from_db_time(job_row["received_at"]) or from_db_time(job_row["created_at"])
    return recorded_at_columns(
        received_at=received_at, sent_at=from_db_time(job_row["sent_at"]), probe=report
    )
