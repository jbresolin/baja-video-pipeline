""" "Ver clipe": a small preview of every rendered clip, watched in the browser (LIVESTACK-52).

Reviewers could not see what was rendered (orientation, cuts, overlay,
result pill, clock): the clip exists only on the worker that rendered it,
the master's archive and the studio. So:

  * A process task carries `preview_upload_url` while the Settings switch
    "Gerar prévia dos clipes" is on (task_rules.py). A 0.7.0 worker
    (RenderFeature.CLIP_PREVIEW) delivers the clip as always, then makes a
    480p preview of it with the review proxy's settings and uploads it,
    chunked and resumable, to POST /jobs/{id}/clip-preview with the
    Clip-SHA256 header naming the clip it shows. There is no lease by then
    (the delivery ended it): only the worker that rendered that clip
    version may send it, and only while it is the job's newest version.
  * The file is previews/<job_id>.<clip sha256>.mp4, served by Caddy :443
    /previews/* to signed-in users like the proxies. One name per version,
    so a reprocessed clip's preview never overwrites the file a browser is
    playing; once it arrives, the older version's file is deleted.
  * Eviction: like proxies, only when the disk is below the eviction
    threshold, finished jobs first, oldest first (after the proxies, which
    a holder can make again; a preview cannot be made again). Previews of
    versions that are no longer the newest go at once.

The job page and the clips page show the preview or say why there is none
(ClipPreviewState); clip_preview_status.preview_status is the one place
that decides.
"""

import logging
import sqlite3
import time
from collections.abc import Mapping

from orchestrator.context import OrchestratorContext
from orchestrator.domain.clip_preview_status import newest_version_row
from orchestrator.domain.job_view import require_job_row
from orchestrator.domain.uploads import parse_upload_headers, receive_chunk
from orchestrator.errors import ApiError, forbidden, validation_error
from orchestrator.repositories import clips as clips_repo
from orchestrator.repositories import events as events_repo
from orchestrator.repositories import jobs as jobs_repo
from orchestrator.storage import VpsLayout, delete_file_and_partials
from shared.models import ChunkAccepted, ErrorCode
from shared.protocol import CLIP_SHA256_HEADER

logger = logging.getLogger(__name__)

# A 480p preview of a clip: a few MB per minute. A clip is at most a whole
# raw (~2 GB at most), so this ceiling is generous.
MAX_PREVIEW_BYTES = 1_000_000_000
SHA256_HEX_LENGTH = 64
# The cleanup leaves preview files younger than this alone (see
# _delete_stale_preview_files), and deletes half-uploaded ones older than a day.
FRESH_FILE_SECONDS = 600
ABANDONED_UPLOAD_SECONDS = 24 * 3600


def _clip_sha256_header(headers: Mapping[str, str]) -> str:
    value = (headers.get(CLIP_SHA256_HEADER) or "").strip().lower()
    if len(value) != SHA256_HEX_LENGTH or any(char not in "0123456789abcdef" for char in value):
        raise validation_error(f"{CLIP_SHA256_HEADER} must be the clip's SHA-256 (64 hex)")
    return value


def _check_preview_sender(
    connection: sqlite3.Connection, job_id: str, worker_row: sqlite3.Row, clip_sha256: str
) -> None:
    """Only the worker that rendered the clip, and only for the newest version."""
    version_row = clips_repo.get_version_by_sha(connection, job_id, clip_sha256)
    if version_row is None:
        raise ApiError(409, ErrorCode.CONFLICT, "that is not a clip of this job")
    if version_row["rendered_by"] != worker_row["id"]:
        raise forbidden("only the worker that rendered this clip sends its preview")
    newest = newest_version_row(connection, job_id)
    if newest["sha256"] != clip_sha256:
        raise ApiError(409, ErrorCode.CONFLICT, "a newer clip of this job replaced that one")


def accept_preview_chunk(
    context: OrchestratorContext,
    worker_row: sqlite3.Row,
    job_id: str,
    headers: Mapping[str, str],
    chunk: bytes,
) -> ChunkAccepted:
    """POST /jobs/{id}/clip-preview: one chunk of a clip's preview (no lease)."""
    upload = parse_upload_headers(headers)
    clip_sha256 = _clip_sha256_header(headers)
    with context.database.transaction() as connection:
        require_job_row(connection, job_id)
        _check_preview_sender(connection, job_id, worker_row, clip_sha256)
    path = context.layout.preview_path(job_id, clip_sha256)
    accepted = receive_chunk(context, path, MAX_PREVIEW_BYTES, upload, chunk)
    if not accepted.complete:
        return accepted
    with context.database.transaction() as connection:
        try:
            # A reprocess may have finished while the preview was uploading.
            _check_preview_sender(connection, job_id, worker_row, clip_sha256)
        except ApiError:
            delete_file_and_partials(path)
            raise
        jobs_repo.set_preview_clip_sha256(connection, job_id, clip_sha256)
        for older in context.layout.preview_paths_of_job(job_id):
            if older != path:
                delete_file_and_partials(older)
        events_repo.record_event(
            connection,
            context.clock.now(),
            actor=events_repo.worker_actor(worker_row["id"]),
            kind="clip_preview_uploaded",
            job_id=job_id,
            worker_id=worker_row["id"],
            reason=f"preview of clip {clip_sha256[:12]}… ({upload.total_length} bytes)",
        )
    return accepted


def evict_previews_once(context: OrchestratorContext) -> list[str]:
    """Delete previews nobody needs, then (disk tight) the oldest ones.

    1. A preview of a version that is no longer the newest (a reprocess
       finished and its own preview came or will come; older versions keep
       none), or of a job whose preview column was cleared: always.
    2. Below the proxy eviction threshold of free disk: previews of
       finished jobs (done, discarded) first, oldest first, then any, until
       there is room. The page then says "evicted".
    Returns the ids of the jobs whose current preview was evicted (step 2).
    """
    layout = context.layout
    evicted = []
    with context.database.transaction() as connection:
        now = context.clock.now()
        rows = jobs_repo.list_job_rows_with_preview(connection)
        current_paths = set()
        for row in rows:
            newest = newest_version_row(connection, row["id"])
            if newest is not None and newest["sha256"] == row["preview_clip_sha256"]:
                current_paths.add(layout.preview_path(row["id"], row["preview_clip_sha256"]))
        _delete_stale_preview_files(layout, current_paths)
        minimum_free = context.admin_settings.proxy_evict_min_free_bytes
        for row in rows:
            if layout.free_bytes() >= minimum_free:
                break
            path = layout.preview_path(row["id"], row["preview_clip_sha256"])
            if path not in current_paths or not path.exists():
                continue
            delete_file_and_partials(path)
            events_repo.record_event(
                connection,
                now,
                actor=events_repo.SYSTEM_ACTOR,
                kind="clip_preview_evicted",
                job_id=row["id"],
                reason="VPS disk below threshold",
            )
            evicted.append(row["id"])
    return evicted


def _delete_stale_preview_files(layout: VpsLayout, current_paths: set) -> None:
    """Every preview file that is not some job's current one, and abandoned uploads.

    A file younger than FRESH_FILE_SECONDS is left alone: an upload that has
    just completed is renamed into place a moment before its job row says
    so, and must not be taken for a stale one in between.
    """
    if not layout.previews_dir.exists():
        return
    now = time.time()
    for path in layout.previews_dir.iterdir():
        age_seconds = now - path.stat().st_mtime
        if path.name.startswith("."):
            # A half-uploaded preview (".<name>.part", ".part.json") nobody finished.
            if age_seconds > ABANDONED_UPLOAD_SECONDS:
                path.unlink(missing_ok=True)
            continue
        if path in current_paths or age_seconds < FRESH_FILE_SECONDS:
            continue
        delete_file_and_partials(path)
        logger.info("deleted the preview of an older clip version: %s", path.name)
