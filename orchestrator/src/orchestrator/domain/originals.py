""" "Baixar original": a reviewer downloads the raw (LIVESTACK-41 item 15).

The 480p proxy is too small to read a car number or judge a close finish.
The browser cannot reach the workers, and the VPS deletes its inbox copy
once two holders have the raw (design §10: transit only). So:

  * POST /jobs/{id}/original (a reviewer): remember the request. If the
    inbox still has the raw, it is hard-linked into originals/ at once
    (same disk, no copy) and the link works immediately. Otherwise a
    holder is offered `upload_original` (master first, then any online
    holder) and uploads it, chunked and resumable like a clip, to
    POST /jobs/{id}/original-upload.
  * GET /jobs/{id}/original: where that stands (not requested, preparing,
    ready with a link, or no holder online).
  * Downloads: Caddy :443 /original-downloads/<job_id>/<readable raw name>
    → originals/<job_id>.<ext>, same sign-in check as proxies, saved
    under the raw's readable name (item 12).
  * Originals are temporary: kept `originals_keep_hours` (Settings, 24 h)
    after the last request, and evicted earlier, oldest request first,
    when the folder passes `originals_cap_gb` (Settings, 10 GB) or the disk
    passes the proxy eviction threshold. An original is never a holder
    and never a source for processing (nothing but this module reads it).
"""

import logging
import os
import sqlite3
from datetime import datetime

from orchestrator.context import OrchestratorContext
from orchestrator.db.database import from_db_time
from orchestrator.domain.fleet_snapshot import FleetSnapshot
from orchestrator.domain.job_view import require_job_row
from orchestrator.domain.task_decision import TaskDecision, refuse
from orchestrator.repositories import events as events_repo
from orchestrator.repositories import jobs as jobs_repo
from orchestrator.repositories import leases as leases_repo
from orchestrator.storage import VpsLayout, delete_file_and_partials
from shared.models import (
    ErrorCode,
    HolderStatus,
    OriginalDownload,
    OriginalState,
    TaskKind,
    UploadOriginalTask,
    WorkerRole,
)

logger = logging.getLogger(__name__)


def original_file_name(job_row: sqlite3.Row) -> str:
    """The download's name: the raw's readable name, else "<job_id><ext>" (older jobs)."""
    return job_row["raw_file_name"] or f"{job_row['id']}{job_row['raw_extension']}"


def _request_is_live(context: OrchestratorContext, job_row: sqlite3.Row, now: datetime) -> bool:
    requested_at = from_db_time(job_row["original_requested_at"])
    keep = context.admin_settings.originals_keep
    return requested_at is not None and now - requested_at < keep


def _online_holders(snapshot: FleetSnapshot, connection: sqlite3.Connection, job_id: str):
    """Confirmed holders that are online and not paused, master first."""
    holders = [
        holder.worker_id
        for holder in leases_repo.list_holders(connection, job_id)
        if holder.status == HolderStatus.CONFIRMED
        and snapshot.is_available_for_work(holder.worker_id)
    ]
    holders.sort(key=lambda worker_id: not snapshot.is_master(worker_id))
    return holders


def original_status(
    context: OrchestratorContext, connection: sqlite3.Connection, job_row: sqlite3.Row
) -> OriginalDownload:
    now = context.clock.now()
    path = context.layout.original_path(job_row["id"], job_row["raw_extension"])
    if path.exists():
        requested_at = from_db_time(job_row["original_requested_at"]) or now
        name = original_file_name(job_row)
        return OriginalDownload(
            state=OriginalState.READY,
            download_url=VpsLayout.original_download_public_path(job_row["id"], name),
            file_name=name,
            size_bytes=path.stat().st_size,
            expires_at=requested_at + context.admin_settings.originals_keep,
        )
    if not _request_is_live(context, job_row, now):
        return OriginalDownload(state=OriginalState.NOT_REQUESTED)
    snapshot = FleetSnapshot.take(connection, now)
    if _online_holders(snapshot, connection, job_row["id"]):
        return OriginalDownload(state=OriginalState.PREPARING)
    return OriginalDownload(state=OriginalState.NO_HOLDER_ONLINE)


def get_original(context: OrchestratorContext, job_id: str) -> OriginalDownload:
    """GET /jobs/{id}/original: no side effects."""
    with context.database.transaction() as connection:
        return original_status(context, connection, require_job_row(connection, job_id))


def request_original(context: OrchestratorContext, job_id: str, username: str) -> OriginalDownload:
    """POST /jobs/{id}/original: ask for it (again); the 24 h start over."""
    with context.database.transaction() as connection:
        now = context.clock.now()
        job_row = require_job_row(connection, job_id)
        jobs_repo.set_original_requested_at(connection, job_id, now)
        linked = _link_from_inbox(context.layout, job_row)
        events_repo.record_event(
            connection,
            now,
            actor=events_repo.reviewer_actor(username),
            kind="original_requested",
            job_id=job_id,
            reason="on the VPS at once (inbox copy)" if linked else "asked a holder to upload it",
        )
        return original_status(context, connection, require_job_row(connection, job_id))


def _link_from_inbox(layout: VpsLayout, job_row: sqlite3.Row) -> bool:
    """While the inbox still has the raw, a hard link makes the original available at once.

    A hard link is a second name for the same bytes: no copy, no extra
    disk, and the inbox cleanup deleting its name does not affect it.
    """
    target = layout.original_path(job_row["id"], job_row["raw_extension"])
    if target.exists():
        return True
    source = layout.inbox_path(job_row["id"], job_row["raw_extension"])
    if not source.exists():
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(source, target)
    except OSError as error:
        # Another filesystem, or links not allowed: a holder uploads it instead.
        logger.warning("could not link the inbox raw into originals/: %s", error)
        return False
    return True


def evaluate_upload_original(
    connection: sqlite3.Connection,
    context_layout: VpsLayout,
    snapshot: FleetSnapshot,
    job_row: sqlite3.Row,
    worker_row: sqlite3.Row,
) -> TaskDecision:
    """May this worker upload this job's original now? (task_rules.evaluate_task)

    A request counts until the sweeper clears it (originals_keep_hours
    after it was made, background/sweepers.py).
    """
    job_id = job_row["id"]
    if job_row["original_requested_at"] is None:
        return refuse(ErrorCode.NOT_CLAIMABLE, "nobody asked for this original")
    if context_layout.original_path(job_id, job_row["raw_extension"]).exists():
        return refuse(ErrorCode.NOT_CLAIMABLE, "the original is already on the VPS")
    if worker_row["role"] == WorkerRole.STUDIO:
        return refuse(ErrorCode.NOT_CLAIMABLE, "the studio holds no raws")
    candidates = _online_holders(snapshot, connection, job_id)
    excluded = {
        worker_id
        for worker_id in candidates
        if leases_repo.is_task_excluded(connection, job_id, worker_id, TaskKind.UPLOAD_ORIGINAL)
    }
    candidates = [worker_id for worker_id in candidates if worker_id not in excluded]
    if worker_row["id"] not in candidates:
        return refuse(ErrorCode.NOT_CLAIMABLE, "only an online holder of the raw can upload it")
    if candidates[0] != worker_row["id"] and snapshot.is_master(candidates[0]):
        return refuse(ErrorCode.NOT_CLAIMABLE, "the master uploads it")
    for lease in leases_repo.list_leases_for_job(connection, job_id, TaskKind.UPLOAD_ORIGINAL):
        if lease.worker_id != worker_row["id"]:
            return refuse(ErrorCode.ALREADY_CLAIMED, f"worker {lease.worker_id} is uploading it")
    return TaskDecision(
        UploadOriginalTask(
            job_id=job_id,
            raw_sha256=job_row["sha256"],
            size_bytes=job_row["size_bytes"],
            upload_url=context_layout.api_url(f"/jobs/{job_id}/original-upload"),
        )
    )


def sweep_originals(context: OrchestratorContext) -> list[str]:
    """Delete expired originals, then the oldest-requested ones while over a cap.

    Caps: the folder's own (originals_cap_gb) and the disk's free-space
    threshold shared with proxies (proxy_evict_min_free_gb). Returns the
    job ids whose original was deleted.
    """
    layout = context.layout
    settings = context.admin_settings
    deleted = []
    with context.database.transaction() as connection:
        now = context.clock.now()
        rows = jobs_repo.list_original_request_rows(connection)
        on_disk = [
            row for row in rows if layout.original_path(row["id"], row["raw_extension"]).exists()
        ]
        for row in rows:
            if not _request_is_live(context, row, now):
                path = layout.original_path(row["id"], row["raw_extension"])
                if delete_file_and_partials(path):
                    deleted.append(row["id"])
                jobs_repo.set_original_requested_at(connection, row["id"], None)
        remaining = [row for row in on_disk if row["id"] not in deleted]
        # Oldest request first (rows are ordered that way).
        for row in remaining:
            over_cap = layout.originals_bytes() > settings.originals_cap_bytes
            disk_tight = layout.free_bytes() < settings.proxy_evict_min_free_bytes
            if not over_cap and not disk_tight:
                break
            delete_file_and_partials(layout.original_path(row["id"], row["raw_extension"]))
            jobs_repo.set_original_requested_at(connection, row["id"], None)
            deleted.append(row["id"])
        for job_id in deleted:
            events_repo.record_event(
                connection,
                now,
                actor=events_repo.SYSTEM_ACTOR,
                kind="original_evicted",
                job_id=job_id,
                reason="temporary original removed from the VPS",
            )
    return deleted
