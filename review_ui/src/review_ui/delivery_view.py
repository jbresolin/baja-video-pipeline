"""Where a finished clip is: studio, VPS outbox, the master's archive (LIVESTACK-38 item 5).

With no studio online the clip goes to the VPS outbox; the studio later
pulls it; the worker that rendered it keeps a copy, and the master
archives every version. Nothing in the UI said so, so reviewers could not
tell whether a clip "went anywhere". These sentences (catalog keys
"delivery.*") appear in the job page's status strip and the clips page.
"""

from dataclasses import dataclass
from datetime import datetime

from review_ui.catalog import text
from shared.models import DeliveryRoute, Job, JobSummary

IN_OUTBOX_STUDIO_OFFLINE = text("delivery.in_outbox_studio_offline")
OUTBOX_COPY_UNTIL_ARCHIVED = text("delivery.outbox_copy_until_archived")
ROUTE_TEXT = {route: text(f"delivery.route.{route.value}") for route in DeliveryRoute}


@dataclass(frozen=True)
class DeliveryNote:
    # "info", "success" or "warning" (the status strip's colours).
    level: str
    text: str
    # A time to show after the text, as a São Paulo time (m.time).
    at: datetime | None = None


def delivery_notes(job: Job) -> list[DeliveryNote]:
    """Sentences about the current clip; empty while there is none."""
    clip = job.clip
    if clip is None:
        return []
    notes = []
    if clip.delivered_at is None:
        if clip.in_outbox:
            notes.append(DeliveryNote("warning", IN_OUTBOX_STUDIO_OFFLINE))
    else:
        route = ROUTE_TEXT[clip.route]
        notes.append(
            DeliveryNote(
                "success",
                text("delivery.delivered_at", route=route, path=clip.relative_path),
                at=clip.delivered_at,
            )
        )
        if clip.in_outbox:
            notes.append(DeliveryNote("info", OUTBOX_COPY_UNTIL_ARCHIVED))
    notes.extend(archive_notes(job))
    return notes


def archive_notes(job: Job) -> list[DeliveryNote]:
    """Is the current clip in the master's archive? And the older versions, if any?"""
    current = next((v for v in job.clip_versions if v.sha256 == job.clip.sha256), None)
    if current is not None and current.archived_at is not None:
        notes = [
            DeliveryNote(
                "success",
                text("delivery.archived_at", path=current.archive_relative_path),
                at=current.archived_at,
            )
        ]
    else:
        notes = [DeliveryNote("info", text("delivery.not_archived"))]
    older = [v for v in job.clip_versions if current is None or v.version != current.version]
    if older:
        archived_older = sum(1 for v in older if v.archived_at is not None)
        notes.append(
            DeliveryNote(
                "info",
                text("delivery.older_versions", archived=archived_older, total=len(older)),
            )
        )
    return notes


def outbox_waiting(items: list[JobSummary]) -> list[JobSummary]:
    """Jobs whose clip waits in the outbox for the studio (state uploaded, file there)."""
    return [item for item in items if item.clip_download_url]
