"""Turn a jobs row (+ its holders and leases) into the API's Job model."""

import json
import sqlite3
from pathlib import PurePosixPath

from orchestrator.db.database import from_db_time
from orchestrator.domain.pass_results import pass_result_for_job
from orchestrator.domain.render_inputs import compare_newest_clip
from orchestrator.errors import not_found
from orchestrator.repositories import clips as clips_repo
from orchestrator.repositories import jobs as jobs_repo
from orchestrator.repositories import leases as leases_repo
from orchestrator.storage import VpsLayout
from shared.framing import display_size, is_vertical
from shared.layout import (
    archive_relative_path,
    unversioned_clip_relative_path,
    versioned_clip_relative_path,
)
from shared.models import (
    ClipInfo,
    ClipVersion,
    DeliveryRoute,
    Framing,
    IntakeSource,
    Job,
    JobFailure,
    JobState,
    ProbeReport,
    ReceivedAs,
    RecordedAtSource,
    RenderInputs,
    TaskKind,
)


def _optional_bool(value: int | None) -> bool | None:
    return None if value is None else bool(value)


def _media_info_from_row(row: sqlite3.Row) -> ProbeReport | None:
    text = row["media_info_json"]
    return None if text is None else ProbeReport.model_validate(json.loads(text))


def clip_versions_of(connection: sqlite3.Connection, job_id: str) -> list[ClipVersion]:
    return [
        ClipVersion(
            version=version["version"],
            sha256=version["sha256"],
            size_bytes=version["size_bytes"],
            relative_path=version["relative_path"],
            archive_relative_path=archive_relative_path(
                version["relative_path"], version["version"]
            ),
            rendered_by_worker_id=version["rendered_by"],
            rendered_at=from_db_time(version["rendered_at"]),
            archived_at=from_db_time(version["archived_at"]),
            render_inputs=(
                RenderInputs.model_validate_json(version["render_inputs_json"])
                if version["render_inputs_json"] is not None
                else None
            ),
        )
        for version in clips_repo.list_versions(connection, job_id)
    ]


def vertical_after_rotation(row: sqlite3.Row) -> bool | None:
    """Taller than wide once the reviewer's "Girar" is applied; None while the size is unknown."""
    if row["display_width"] is None or row["display_height"] is None:
        return None
    width, height = display_size(row["display_width"], row["display_height"], row["rotation"])
    return is_vertical(width, height)


def clip_download_file_name(relative_path: str, version: int) -> str:
    """ "enduro-042-p3.mp4" for version 1, "enduro-042-p3-v2.mp4" after a reprocess.

    `relative_path` may already be the version's name (the studio path of
    a reprocess, LIVESTACK-43); the version suffix is never doubled.
    """
    first_name = unversioned_clip_relative_path(relative_path)
    return PurePosixPath(versioned_clip_relative_path(first_name, version)).name


def job_from_row(connection: sqlite3.Connection, row: sqlite3.Row, layout: VpsLayout) -> Job:
    """`layout` says whether the VPS outbox still has the clip (ClipInfo.in_outbox)."""
    versions = clip_versions_of(connection, row["id"])
    clip = None
    if row["clip_sha256"] is not None:
        current = next((v for v in versions if v.sha256 == row["clip_sha256"]), None)
        in_outbox = layout.outbox_path(row["id"]).exists()
        download_name = clip_download_file_name(
            row["clip_relative_path"], current.version if current else 1
        )
        clip = ClipInfo(
            sha256=row["clip_sha256"],
            size_bytes=row["clip_size_bytes"],
            relative_path=row["clip_relative_path"],
            route=DeliveryRoute(row["clip_route"]),
            rendered_by_worker_id=row["clip_rendered_by"],
            delivered_at=from_db_time(row["clip_delivered_at"]),
            version=current.version if current else 1,
            in_outbox=in_outbox,
            download_url=(
                VpsLayout.clip_download_public_path(row["id"], download_name) if in_outbox else None
            ),
            download_file_name=download_name,
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
            raw_file_name=row["raw_file_name"],
            sent_at=from_db_time(row["sent_at"]),
            received_at=from_db_time(row["received_at"]),
            caption=row["caption"],
        ),
        low_res=bool(row["low_res"]),
        recorded_at=from_db_time(row["recorded_at"]),
        recorded_at_source=(
            RecordedAtSource(row["recorded_at_source"]) if row["recorded_at_source"] else None
        ),
        sha256=row["sha256"],
        size_bytes=row["size_bytes"],
        duration_s=row["duration_s"],
        has_audio=_optional_bool(row["has_audio"]),
        media_info=_media_info_from_row(row),
        frame_rate=row["frame_rate"],
        display_width=row["display_width"],
        display_height=row["display_height"],
        vertical=vertical_after_rotation(row),
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
        rotation=row["rotation"],
        framing=Framing(row["framing"]),
        crop_position=row["crop_position"],
        no_audio=bool(row["no_audio"]),
        show_pass_result=_optional_bool(row["show_pass_result"]),
        reviewed_by=row["reviewed_by"],
        reviewed_at=from_db_time(row["reviewed_at"]),
        clip=clip,
        clip_versions=versions,
        # Does the newest clip still match the review? (LIVESTACK-43)
        clip_inputs=compare_newest_clip(connection, row) if versions else None,
        # The official result of the passagem, from the latest fetch (LIVESTACK-47).
        pass_result=pass_result_for_job(connection, row),
        failure=failure,
        failed_from=JobState(row["failed_from"]) if row["failed_from"] else None,
        discarded_from=JobState(row["discarded_from"]) if row["discarded_from"] else None,
        discard_grace_ends_at=from_db_time(row["discard_grace_ends_at"]),
        waiting_reason=row["waiting_reason"],
    )


def require_job_row(connection: sqlite3.Connection, job_id: str) -> sqlite3.Row:
    """The job's row, or a 404 not_found ApiError."""
    row = jobs_repo.get_job_row(connection, job_id)
    if row is None:
        raise not_found("job")
    return row


def load_job(connection: sqlite3.Connection, job_id: str, layout: VpsLayout) -> Job:
    return job_from_row(connection, require_job_row(connection, job_id), layout)
