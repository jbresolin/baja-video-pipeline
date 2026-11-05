"""archive_clip: the master keeps a copy of every finished clip (design §11 as amended).

Offered only to the master, and only after everything else it could do
(live tasks, then raw backfill): see offer_archive_clip. One task copies
one clip *version*; reprocessed clips are versions, not replacements, so
the master ends up with <passagem>.mp4, <passagem>.v2.mp4, ...

Sources, in order (the master tries them one by one):
1. none at all when the master itself keeps that clip (it rendered it);
2. the file server (:8710) of each online worker that keeps it, the one
   that rendered it first;
3. the VPS outbox, while it still has that exact clip.
A version with no reachable source is skipped until one comes back, and
one whose copy failed waits ARCHIVE_RETRY_AFTER (no exclusion: the master
is the only machine that archives).
"""

import sqlite3
from datetime import timedelta

from orchestrator.db.database import from_db_time
from orchestrator.domain.fleet_snapshot import FleetSnapshot
from orchestrator.domain.task_decision import TaskDecision, refuse
from orchestrator.repositories import clips as clips_repo
from orchestrator.repositories import leases as leases_repo
from orchestrator.storage import VpsLayout
from shared.layout import archive_relative_path
from shared.models import ArchiveClipTask, ErrorCode, JobState, TaskKind, WorkerRole
from shared.protocol import DEFAULT_WORKER_FILE_SERVER_PORT

# After the master reported archive_clip failed (disk error, every source
# sent a wrong file), that job's versions wait this long before the next try.
ARCHIVE_RETRY_AFTER = timedelta(minutes=10)


def peer_clip_url(worker_row: sqlite3.Row, job_id: str, sha256: str) -> str | None:
    """http://<tailnet address>:8710/clip/<job_id>?sha256=<sha>, or None without an address."""
    address = worker_row["tailnet_address"]
    if not address:
        return None
    port = worker_row["file_server_port"] or DEFAULT_WORKER_FILE_SERVER_PORT
    return f"http://{address}:{port}/clip/{job_id}?sha256={sha256}"


def outbox_has_version(layout: VpsLayout, job_row: sqlite3.Row, sha256: str) -> bool:
    """The outbox file is always the job's current clip, so it is this version only then."""
    return job_row["clip_sha256"] == sha256 and layout.outbox_path(job_row["id"]).exists()


def clip_sources(
    connection: sqlite3.Connection,
    layout: VpsLayout,
    snapshot: FleetSnapshot,
    job_row: sqlite3.Row,
    version_row: sqlite3.Row,
    master_id: str,
) -> list[str] | None:
    """Download URLs for one version; [] = the master has it; None = nowhere reachable."""
    sha256 = version_row["sha256"]
    keepers = clips_repo.list_workers_keeping(connection, sha256)
    if master_id in keepers:
        return []
    # The renderer first: it is the one most likely to still have the file.
    keepers.sort(key=lambda worker_id: worker_id != version_row["rendered_by"])
    urls = []
    for worker_id in keepers:
        if not snapshot.is_online(worker_id):
            continue
        url = peer_clip_url(snapshot.workers_by_id[worker_id], job_row["id"], sha256)
        if url:
            urls.append(url)
    if outbox_has_version(layout, job_row, sha256):
        urls.append(layout.outbox_download_url(job_row["id"]))
    return urls or None


def evaluate_archive_clip(
    connection: sqlite3.Connection,
    layout: VpsLayout,
    snapshot: FleetSnapshot,
    job_row: sqlite3.Row,
    worker_row: sqlite3.Row,
) -> TaskDecision:
    """The oldest version of this job the master lacks and can fetch, as a task."""
    if worker_row["role"] != WorkerRole.MASTER:
        return refuse(ErrorCode.NOT_CLAIMABLE, "only the master archives clips")
    if job_row["state"] == JobState.DISCARDED:
        return refuse(ErrorCode.NOT_CLAIMABLE, "the job was discarded")
    for lease in leases_repo.list_leases_for_job(connection, job_row["id"], TaskKind.ARCHIVE_CLIP):
        if lease.worker_id != worker_row["id"]:
            return refuse(ErrorCode.ALREADY_CLAIMED, "another master is archiving it")
    for version_row in clips_repo.list_versions(connection, job_row["id"]):
        if version_row["archived_at"] is not None:
            continue
        failed_at = from_db_time(version_row["archive_failed_at"])
        if failed_at is not None and snapshot.now - failed_at < ARCHIVE_RETRY_AFTER:
            continue
        urls = clip_sources(connection, layout, snapshot, job_row, version_row, worker_row["id"])
        if urls is None:
            continue
        return TaskDecision(
            ArchiveClipTask(
                job_id=job_row["id"],
                version=version_row["version"],
                sha256=version_row["sha256"],
                size_bytes=version_row["size_bytes"],
                archive_relative_path=archive_relative_path(
                    version_row["relative_path"], version_row["version"]
                ),
                download_urls=urls,
            )
        )
    return refuse(ErrorCode.NOT_CLAIMABLE, "no unarchived clip of this job is reachable")


def offer_archive_clip(
    connection: sqlite3.Connection,
    layout: VpsLayout,
    snapshot: FleetSnapshot,
    worker_row: sqlite3.Row,
    accepted_kinds: set[TaskKind],
) -> ArchiveClipTask | None:
    """For /next, after everything else: one archive task for the master, or None."""
    if worker_row["role"] != WorkerRole.MASTER or TaskKind.ARCHIVE_CLIP not in accepted_kinds:
        return None
    if any(
        lease.task_kind == TaskKind.ARCHIVE_CLIP
        for lease in leases_repo.list_leases_for_worker(connection, worker_row["id"])
    ):
        return None  # one archive copy at a time, like every other kind
    for job_row in clips_repo.list_jobs_with_unarchived_clips(connection):
        decision = evaluate_archive_clip(connection, layout, snapshot, job_row, worker_row)
        if decision.task is not None:
            return decision.task
    return None
