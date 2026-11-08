""" "Baixar clipe": sending a requested clip, and deleting the copy later (LIVESTACK-52).

clip_downloads.py gets the clip onto the VPS (requested_clips/<job_id>.mp4);
this module is what happens to it there:

  * The download, GET /requested-clips/<job_id>/<clip name> (Caddy passes
    it to the orchestrator; signed-in users only), is served by the
    orchestrator and not by Caddy's file server, because only the server
    sending the bytes knows when a download *finished*:
    `requested_clip_chunks` records the first complete download once the
    file's last byte has been handed over. A download that breaks off
    before that does not count.
  * The copy is temporary: deleted `clip_download_keep_minutes` (Settings,
    30) after its first complete download, so a broken one can be retried,
    or `clip_download_unused_hours` (Settings, 24) after the request if
    nobody downloads it, and earlier when the disk is below the proxy
    eviction threshold (downloaded ones first, then the oldest request).
"""

import sqlite3
from collections.abc import Callable, Iterator
from datetime import datetime
from pathlib import Path

from orchestrator.context import OrchestratorContext
from orchestrator.db.database import from_db_time
from orchestrator.domain.clip_downloads import clip_file_name, requested_version_row
from orchestrator.errors import ApiError
from orchestrator.repositories import events as events_repo
from orchestrator.repositories import jobs as jobs_repo
from orchestrator.storage import delete_file_and_partials
from shared.models import ErrorCode

# Bytes read from disk per piece of a download response.
DOWNLOAD_PIECE_BYTES = 1024 * 1024


class RequestedClipFile:
    """What GET /requested-clips/<job_id>/<name> sends: the file, its size and name."""

    def __init__(self, path: Path, size_bytes: int, file_name: str) -> None:
        self.path = path
        self.size_bytes = size_bytes
        self.file_name = file_name


def open_requested_clip(context: OrchestratorContext, job_id: str) -> RequestedClipFile:
    """The ready copy of a requested clip, or 404 (never asked, not uploaded yet, deleted)."""
    with context.database.transaction() as connection:
        job_row = jobs_repo.get_job_row(connection, job_id)
        version_row = None if job_row is None else requested_version_row(connection, job_row)
    path = context.layout.requested_clip_path(job_id)
    if version_row is None or not path.exists():
        raise ApiError(404, ErrorCode.NOT_FOUND, "this clip is not ready for download")
    return RequestedClipFile(path, path.stat().st_size, clip_file_name(version_row))


def requested_clip_chunks(
    path: Path, first_byte: int, last_byte: int, on_complete: Callable[[], None]
) -> Iterator[bytes]:
    """The bytes first_byte..last_byte of the file, then `on_complete()` if that was the end.

    The web layer sends each piece before asking for the next, so the line
    after the last `yield` runs only once the file's last byte has been
    handed over. If the browser goes away first, the generator is closed
    at a `yield` and that line never runs: a broken download does not
    count. A range that stops before the end of the file never counts; one
    that reaches it does (a resumed download finishing).
    """
    file_size = path.stat().st_size
    remaining = last_byte - first_byte + 1
    with path.open("rb") as source:
        source.seek(first_byte)
        while remaining > 0:
            piece = source.read(min(DOWNLOAD_PIECE_BYTES, remaining))
            if not piece:
                return  # the file got shorter: not a complete download
            remaining -= len(piece)
            yield piece
    if last_byte == file_size - 1:
        on_complete()


def record_complete_download(context: OrchestratorContext, job_id: str, username: str) -> None:
    """The first complete download starts the 30 minutes; later ones change nothing."""
    with context.database.transaction() as connection:
        now = context.clock.now()
        if jobs_repo.set_clip_download_completed(connection, job_id, now):
            events_repo.record_event(
                connection,
                now,
                actor=events_repo.reviewer_actor(username),
                kind="clip_downloaded",
                job_id=job_id,
                reason="first complete download; the VPS copy goes soon",
            )


# --- Deleting copies ---


def sweep_requested_clips(context: OrchestratorContext) -> list[str]:
    """Delete copies whose time is up, then (disk tight) more. Returns their job ids.

    Time is up `clip_download_keep` after the first complete download, or
    `clip_download_unused` after the request when nobody downloaded it.
    Below the proxy eviction threshold of free disk, downloaded copies go
    first, then the oldest requests, until there is room.
    """
    layout = context.layout
    settings = context.admin_settings
    deleted: dict[str, str] = {}
    with context.database.transaction() as connection:
        now = context.clock.now()
        rows = jobs_repo.list_clip_download_rows(connection)
        for row in rows:
            reason = _time_is_up(context, row, now)
            if reason is not None:
                deleted[row["id"]] = reason
        remaining = [
            row
            for row in rows
            if row["id"] not in deleted and layout.requested_clip_path(row["id"]).exists()
        ]
        remaining.sort(key=lambda row: row["clip_download_completed_at"] is None)
        free_bytes = layout.free_bytes()
        for row in remaining:
            if free_bytes >= settings.proxy_evict_min_free_bytes:
                break
            free_bytes += layout.requested_clip_path(row["id"]).stat().st_size
            deleted[row["id"]] = "VPS disk below threshold"
        for job_id, reason in deleted.items():
            delete_file_and_partials(layout.requested_clip_path(job_id))
            jobs_repo.clear_clip_download_request(connection, job_id)
            events_repo.record_event(
                connection,
                now,
                actor=events_repo.SYSTEM_ACTOR,
                kind="clip_download_deleted",
                job_id=job_id,
                reason=reason,
            )
    return list(deleted)


def _time_is_up(context: OrchestratorContext, row: sqlite3.Row, now: datetime) -> str | None:
    settings = context.admin_settings
    completed_at = from_db_time(row["clip_download_completed_at"])
    if completed_at is not None:
        if now - completed_at >= settings.clip_download_keep:
            return "downloaded; the time to retry is over"
        return None
    requested_at = from_db_time(row["clip_download_requested_at"])
    if now - requested_at >= settings.clip_download_unused:
        return "nobody downloaded it"
    return None
