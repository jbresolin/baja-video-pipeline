""" "Baixar clipe": a reviewer downloads the finished clip (LIVESTACK-52).

The same request pattern as "Baixar original" (originals.py):

  * POST /jobs/{id}/clip-download (a reviewer): remember the request for
    the job's newest clip version. If the VPS outbox still has exactly that
    clip, it is hard-linked into requested_clips/ at once (no copy) and
    the link works immediately. Otherwise a PC that has it is offered
    `upload_clip` and uploads it, chunked and resumable, to
    POST /jobs/{id}/clip-download-upload.
  * Who uploads it (`upload_sources`), in tiers: the master, when it
    archived that version or keeps it; then the worker that rendered it
    (or any other worker keeping it); the studio only when nobody else can,
    and only while it is not delivering (no deliver lease, no clip on its
    way to it). Each must be online, not paused, and have declared
    RenderFeature.UPLOAD_CLIP (installer 0.7.0): an older worker would
    refuse a task kind it does not know.
  * GET /jobs/{id}/clip-download: where that stands.
  * The download itself and when the VPS copy goes: requested_clips.py.
"""

import logging
import os
import sqlite3
from collections.abc import Mapping

from orchestrator.context import OrchestratorContext
from orchestrator.db.database import from_db_time
from orchestrator.domain.archive_rules import outbox_has_version
from orchestrator.domain.clip_preview_status import newest_version_row
from orchestrator.domain.fleet_snapshot import FleetSnapshot
from orchestrator.domain.job_view import clip_download_file_name, require_job_row
from orchestrator.domain.render_features import worker_render_features
from orchestrator.domain.task_decision import TaskDecision, refuse
from orchestrator.domain.uploads import (
    MAX_CLIP_BYTES,
    check_upload_lease,
    finish_upload_under_lease,
    parse_upload_headers,
    receive_chunk,
)
from orchestrator.errors import ApiError
from orchestrator.repositories import clips as clips_repo
from orchestrator.repositories import events as events_repo
from orchestrator.repositories import jobs as jobs_repo
from orchestrator.repositories import leases as leases_repo
from orchestrator.storage import VpsLayout, delete_file_and_partials
from shared.layout import archive_relative_path, versioned_clip_relative_path
from shared.models import (
    ChunkAccepted,
    ClipDownload,
    ClipDownloadState,
    ErrorCode,
    JobState,
    RenderFeature,
    TaskKind,
    UploadClipTask,
    WorkerRole,
)

logger = logging.getLogger(__name__)

# --- Which version, named how ---


def requested_version_row(
    connection: sqlite3.Connection, job_row: sqlite3.Row
) -> sqlite3.Row | None:
    """The clip version the request is for (its SHA-256 was fixed at request time)."""
    sha256 = job_row["clip_download_sha256"]
    if sha256 is None:
        return None
    return clips_repo.get_version_by_sha(connection, job_row["id"], sha256)


def clip_file_name(version_row: sqlite3.Row) -> str:
    """The clip's readable name: "suspensao-007-p2.mp4", "…-p2-v2.mp4" after a reprocess."""
    return clip_download_file_name(version_row["relative_path"], version_row["version"])


# --- Status, request ---


def clip_download_status(
    context: OrchestratorContext, connection: sqlite3.Connection, job_row: sqlite3.Row
) -> ClipDownload:
    now = context.clock.now()
    requested_at = from_db_time(job_row["clip_download_requested_at"])
    if requested_at is None:
        return ClipDownload(state=ClipDownloadState.NOT_REQUESTED)
    version_row = requested_version_row(connection, job_row)
    path = context.layout.requested_clip_path(job_row["id"])
    if version_row is not None and path.exists():
        name = clip_file_name(version_row)
        completed_at = from_db_time(job_row["clip_download_completed_at"])
        settings = context.admin_settings
        expires_at = (
            completed_at + settings.clip_download_keep
            if completed_at is not None
            else requested_at + settings.clip_download_unused
        )
        return ClipDownload(
            state=ClipDownloadState.READY,
            download_url=VpsLayout.requested_clip_public_path(job_row["id"], name),
            file_name=name,
            size_bytes=path.stat().st_size,
            version=version_row["version"],
            downloaded_at=completed_at,
            expires_at=expires_at,
        )
    version = version_row["version"] if version_row is not None else None
    snapshot = FleetSnapshot.take(connection, now)
    if version_row is not None and _first_tier(upload_sources(connection, snapshot, job_row)):
        return ClipDownload(state=ClipDownloadState.PREPARING, version=version)
    return ClipDownload(state=ClipDownloadState.NO_SOURCE_ONLINE, version=version)


def get_clip_download(context: OrchestratorContext, job_id: str) -> ClipDownload:
    """GET /jobs/{id}/clip-download: no side effects."""
    with context.database.transaction() as connection:
        return clip_download_status(context, connection, require_job_row(connection, job_id))


def request_clip_download(context: OrchestratorContext, job_id: str, username: str) -> ClipDownload:
    """POST /jobs/{id}/clip-download: ask for the newest clip (again); the times start over."""
    with context.database.transaction() as connection:
        now = context.clock.now()
        job_row = require_job_row(connection, job_id)
        newest = newest_version_row(connection, job_id)
        if newest is None:
            raise ApiError(409, ErrorCode.CONFLICT, "this job has no clip yet")
        path = context.layout.requested_clip_path(job_id)
        if job_row["clip_download_sha256"] != newest["sha256"]:
            # A copy of an older version (a reprocess came since) goes.
            delete_file_and_partials(path)
        jobs_repo.set_clip_download_request(connection, job_id, now, newest["sha256"])
        linked = _link_from_outbox(context.layout, job_row, newest["sha256"])
        events_repo.record_event(
            connection,
            now,
            actor=events_repo.reviewer_actor(username),
            kind="clip_download_requested",
            job_id=job_id,
            reason=(
                f"version {newest['version']}: "
                + ("on the VPS at once (outbox copy)" if linked else "asked a PC to upload it")
            ),
        )
        return clip_download_status(context, connection, require_job_row(connection, job_id))


def _link_from_outbox(layout: VpsLayout, job_row: sqlite3.Row, clip_sha256: str) -> bool:
    """While the outbox has exactly this clip, a hard link makes it downloadable at once.

    A second name for the same bytes, like originals.py's inbox link: the
    outbox cleanup deleting its own name does not affect it.
    """
    target = layout.requested_clip_path(job_row["id"])
    if target.exists():
        return True
    if not outbox_has_version(layout, job_row, clip_sha256):
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(layout.outbox_path(job_row["id"]), target)
    except OSError as error:
        logger.warning("could not link the outbox clip into requested_clips/: %s", error)
        return False
    return True


# --- Who uploads it ---


def _can_upload_clips(worker_row: sqlite3.Row) -> bool:
    return RenderFeature.UPLOAD_CLIP.value in worker_render_features(worker_row)


def _studio_is_delivering(connection: sqlite3.Connection, studio_id: str) -> bool:
    """A deliver lease (outbox pull), or any clip on its way to the studio
    (a job `uploaded`: pushed and waiting for the ACK, or in the outbox)."""
    if any(
        lease.task_kind == TaskKind.DELIVER
        for lease in leases_repo.list_leases_for_worker(connection, studio_id)
    ):
        return True
    return bool(jobs_repo.list_job_rows_in_states(connection, [JobState.UPLOADED]))


def upload_sources(
    connection: sqlite3.Connection, snapshot: FleetSnapshot, job_row: sqlite3.Row
) -> list[list[str]]:
    """Who may upload the requested clip now, as tiers in order of preference.

    [masters], [workers keeping it, the renderer first], [studio]. A tier
    is empty when nobody in it can; the first non-empty one is asked.
    """
    version_row = requested_version_row(connection, job_row)
    if version_row is None:
        return [[], [], []]
    job_id = job_row["id"]
    sha256 = version_row["sha256"]

    def may_upload(worker_id: str) -> bool:
        row = snapshot.workers_by_id.get(worker_id)
        return (
            row is not None
            and snapshot.is_available_for_work(worker_id)
            and _can_upload_clips(row)
            and not leases_repo.is_task_excluded(
                connection, job_id, worker_id, TaskKind.UPLOAD_CLIP
            )
        )

    keepers = clips_repo.list_workers_keeping(connection, sha256)
    archived = version_row["archived_at"] is not None
    masters = [
        worker_id
        for worker_id, row in snapshot.workers_by_id.items()
        if row["role"] == WorkerRole.MASTER
        and (archived or worker_id in keepers)
        and may_upload(worker_id)
    ]
    workers = [
        worker_id
        for worker_id in keepers
        if worker_id in snapshot.workers_by_id
        and snapshot.workers_by_id[worker_id]["role"] == WorkerRole.WORKER
        and may_upload(worker_id)
    ]
    workers.sort(key=lambda worker_id: worker_id != version_row["rendered_by"])
    delivered_to_studio = (
        job_row["clip_sha256"] == sha256 and job_row["clip_delivered_at"] is not None
    )
    studios = [
        worker_id
        for worker_id, row in snapshot.workers_by_id.items()
        if delivered_to_studio
        and row["role"] == WorkerRole.STUDIO
        and may_upload(worker_id)
        and not _studio_is_delivering(connection, worker_id)
    ]
    return [masters, workers, studios]


def _first_tier(tiers: list[list[str]]) -> list[str]:
    return next((tier for tier in tiers if tier), [])


def evaluate_upload_clip(
    connection: sqlite3.Connection,
    layout: VpsLayout,
    snapshot: FleetSnapshot,
    job_row: sqlite3.Row,
    worker_row: sqlite3.Row,
) -> TaskDecision:
    """May this worker upload this job's requested clip now? (task_rules.evaluate_task)

    A request counts until the sweeper deletes its copy.
    """
    job_id = job_row["id"]
    if job_row["clip_download_requested_at"] is None:
        return refuse(ErrorCode.NOT_CLAIMABLE, "nobody asked for this clip")
    if layout.requested_clip_path(job_id).exists():
        return refuse(ErrorCode.NOT_CLAIMABLE, "the clip is already on the VPS")
    version_row = requested_version_row(connection, job_row)
    if version_row is None:
        return refuse(ErrorCode.NOT_CLAIMABLE, "the requested clip is not a version of this job")
    tiers = upload_sources(connection, snapshot, job_row)
    if not any(worker_row["id"] in tier for tier in tiers):
        return refuse(ErrorCode.NOT_CLAIMABLE, "this PC cannot upload that clip now")
    if worker_row["id"] not in _first_tier(tiers):
        return refuse(ErrorCode.NOT_CLAIMABLE, "another PC comes first for this clip")
    for lease in leases_repo.list_leases_for_job(connection, job_id, TaskKind.UPLOAD_CLIP):
        if lease.worker_id != worker_row["id"]:
            return refuse(ErrorCode.ALREADY_CLAIMED, f"worker {lease.worker_id} is uploading it")
    first_name = version_row["relative_path"]
    return TaskDecision(
        UploadClipTask(
            job_id=job_id,
            version=version_row["version"],
            sha256=version_row["sha256"],
            size_bytes=version_row["size_bytes"],
            archive_relative_path=archive_relative_path(first_name, version_row["version"]),
            studio_relative_path=versioned_clip_relative_path(first_name, version_row["version"]),
            upload_url=layout.api_url(f"/jobs/{job_id}/clip-download-upload"),
        )
    )


# --- The upload ---


def accept_clip_download_chunk(
    context: OrchestratorContext,
    worker_row: sqlite3.Row,
    job_id: str,
    lease_id: str,
    headers: Mapping[str, str],
    chunk: bytes,
) -> ChunkAccepted:
    """POST /jobs/{id}/clip-download-upload: a chunk of the requested clip, under the lease."""
    upload = parse_upload_headers(headers)
    job_row = check_upload_lease(context, worker_row, job_id, lease_id, TaskKind.UPLOAD_CLIP)
    if upload.sha256 != job_row["clip_download_sha256"]:
        raise ApiError(422, ErrorCode.HASH_MISMATCH, "that is not the clip that was asked for")
    path = context.layout.requested_clip_path(job_id)
    accepted = receive_chunk(context, path, MAX_CLIP_BYTES, upload, chunk)
    if not accepted.complete:
        return accepted

    def clip_is_on_the_vps(connection: sqlite3.Connection, job_row: sqlite3.Row) -> None:
        events_repo.record_event(
            connection,
            context.clock.now(),
            actor=events_repo.worker_actor(worker_row["id"]),
            kind="clip_download_uploaded",
            job_id=job_id,
            worker_id=worker_row["id"],
            reason="the clip is ready for download",
        )

    finish_upload_under_lease(
        context, worker_row, job_id, lease_id, TaskKind.UPLOAD_CLIP, clip_is_on_the_vps
    )
    return accepted
