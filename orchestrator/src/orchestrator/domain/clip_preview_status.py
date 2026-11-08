"""Whether the newest clip has a preview to watch, or why not (LIVESTACK-52).

The one place that decides ClipPreviewState, for the job page (job_view)
and the clips page (the manifest). Kept apart from clip_previews.py, which
receives and evicts the files, because job_view cannot import the domain
modules that import it back.
"""

import json
import sqlite3
from datetime import datetime, timedelta

from orchestrator.db.database import from_db_time
from orchestrator.repositories import clips as clips_repo
from orchestrator.repositories import workers as workers_repo
from orchestrator.storage import VpsLayout
from shared.models import ClipPreview, ClipPreviewState, RenderFeature

# After the clip version is recorded, its preview is "being made" for this
# long; after it, the page says none arrived (the worker failed or quit).
PREVIEW_ARRIVES_WITHIN = timedelta(minutes=15)


def newest_version_row(connection: sqlite3.Connection, job_id: str) -> sqlite3.Row | None:
    versions = clips_repo.list_versions(connection, job_id)
    return versions[-1] if versions else None


def preview_status(
    layout: VpsLayout, connection: sqlite3.Connection, job_row: sqlite3.Row, now: datetime
) -> ClipPreview | None:
    """The newest clip's preview, or why it has none; None while the job has no clip."""
    newest = newest_version_row(connection, job_row["id"])
    if newest is None:
        return None
    clip_sha256 = newest["sha256"]
    version = newest["version"]
    if job_row["preview_clip_sha256"] == clip_sha256:
        path = layout.preview_path(job_row["id"], clip_sha256)
        if path.exists():
            return ClipPreview(
                state=ClipPreviewState.READY,
                url=VpsLayout.preview_public_path(job_row["id"], clip_sha256),
                version=version,
                size_bytes=path.stat().st_size,
            )
        # The column stays when the eviction deletes the file.
        return ClipPreview(state=ClipPreviewState.EVICTED, version=version)
    return ClipPreview(state=_why_no_preview(connection, job_row, newest, now), version=version)


def _why_no_preview(
    connection: sqlite3.Connection, job_row: sqlite3.Row, newest: sqlite3.Row, now: datetime
) -> ClipPreviewState:
    if job_row["preview_wanted"] is None:
        # Rendered before previews existed: none will come.
        return ClipPreviewState.MISSING
    if not job_row["preview_wanted"]:
        return ClipPreviewState.SWITCHED_OFF
    renderer = workers_repo.get_worker_row(connection, newest["rendered_by"] or "")
    if renderer is None or not _declared_clip_preview(renderer):
        return ClipPreviewState.OLDER_WORKER
    rendered_at = from_db_time(newest["rendered_at"])
    if rendered_at is not None and now - rendered_at < PREVIEW_ARRIVES_WITHIN:
        return ClipPreviewState.MAKING
    return ClipPreviewState.MISSING


def _declared_clip_preview(worker_row: sqlite3.Row) -> bool:
    """Did this worker's version say it makes previews? (render_features.py's
    worker_render_features, read here without importing that module.)"""
    capabilities = json.loads(worker_row["capabilities_json"] or "{}")
    return RenderFeature.CLIP_PREVIEW.value in (capabilities.get("render_features") or [])
