"""Turn a jobs row (+ its holders and leases) into the API's Job model."""

import sqlite3

from orchestrator.db.database import from_db_time
from orchestrator.errors import not_found
from orchestrator.repositories import jobs as jobs_repo
from orchestrator.repositories import leases as leases_repo
from orchestrator.storage import VpsLayout
from shared.models import (
    ClipInfo,
    DeliveryRoute,
    IntakeSource,
    Job,
    JobFailure,
    JobState,
    ReceivedAs,
    TaskKind,
)


def _optional_bool(value: int | None) -> bool | None:
    return None if value is None else bool(value)


def job_from_row(connection: sqlite3.Connection, row: sqlite3.Row) -> Job:
    clip = None
    if row["clip_sha256"] is not None:
        clip = ClipInfo(
            sha256=row["clip_sha256"],
            size_bytes=row["clip_size_bytes"],
            relative_path=row["clip_relative_path"],
            route=DeliveryRoute(row["clip_route"]),
            rendered_by_worker_id=row["clip_rendered_by"],
            delivered_at=from_db_time(row["clip_delivered_at"]),
        )
    failure = None
    if row["failure_message"] is not None:
        failure = JobFailure(
            worker_id=row["failure_worker_id"],
            task_kind=TaskKind(row["failure_task_kind"]),
            message=row["failure_message"],
            exit_code=row["failure_exit_code"],
            failed_at=from_db_time(row["failure_at"]),
        )
    proxy_available = bool(row["proxy_available"])
    return Job(
        id=row["id"],
        state=JobState(row["state"]),
        version=row["version"],
        created_at=from_db_time(row["created_at"]),
        updated_at=from_db_time(row["updated_at"]),
        source=IntakeSource(
            message_id=row["message_id"],
            sender_jid=row["sender_jid"],
            sender_name=row["sender_name"],
            received_as=ReceivedAs(row["received_as"]),
            original_filename=row["original_filename"],
            mimetype=row["mimetype"],
        ),
        low_res=bool(row["low_res"]),
        sha256=row["sha256"],
        size_bytes=row["size_bytes"],
        duration_s=row["duration_s"],
        has_audio=_optional_bool(row["has_audio"]),
        raw_on_vps=bool(row["raw_on_vps"]),
        holders=leases_repo.list_holders(connection, row["id"]),
        leases=leases_repo.list_leases_for_job(connection, row["id"]),
        proxy_available=proxy_available,
        proxy_url=VpsLayout.proxy_public_path(row["id"]) if proxy_available else None,
        thumbnail_url=(
            VpsLayout.thumbnail_public_path(row["id"]) if row["thumbnail_available"] else None
        ),
        prova=row["prova"],
        car_number=row["car_number"],
        team_name=row["team_name"],
        passagem=row["passagem"],
        show_pass=bool(row["show_pass"]),
        cut_in_s=row["cut_in_s"],
        cut_out_s=row["cut_out_s"],
        reviewed_by=row["reviewed_by"],
        reviewed_at=from_db_time(row["reviewed_at"]),
        clip=clip,
        failure=failure,
        failed_from=JobState(row["failed_from"]) if row["failed_from"] else None,
        waiting_reason=row["waiting_reason"],
    )


def require_job_row(connection: sqlite3.Connection, job_id: str) -> sqlite3.Row:
    """The job's row, or a 404 not_found ApiError."""
    row = jobs_repo.get_job_row(connection, job_id)
    if row is None:
        raise not_found("job")
    return row


def load_job(connection: sqlite3.Connection, job_id: str) -> Job:
    return job_from_row(connection, require_job_row(connection, job_id))
