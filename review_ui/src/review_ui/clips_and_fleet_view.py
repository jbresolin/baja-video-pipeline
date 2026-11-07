"""Shaping data for the clips page (manifest) and the fleet page."""

from collections.abc import Mapping
from dataclasses import dataclass

from review_ui.catalog import has_text, text
from review_ui.event_log_view import EventLine, event_lines_newest_first
from review_ui.formatting import percent, short_id
from review_ui.queue_view import STATES_IN_PIPELINE_ORDER, same_prova
from shared.models import Event, FleetResponse, FleetWorker, ManifestEntry, Worker, WorkerRole

# --- Clips ---


def filter_clips_by_prova(clips: list[ManifestEntry], prova: str) -> list[ManifestEntry]:
    if not prova:
        return clips
    return [clip for clip in clips if same_prova(clip.prova, prova)]


def studio_path(relative_path: str, studio_clip_root: str) -> str:
    """The path to paste into the studio's file browser or OBS.

    Without a configured root this is the manifest's relative path. With a
    Windows root (it contains a backslash) the slashes are turned around.
    """
    if not studio_clip_root:
        return relative_path
    if "\\" in studio_clip_root:
        return studio_clip_root.rstrip("\\/") + "\\" + relative_path.replace("/", "\\")
    return studio_clip_root.rstrip("/") + "/" + relative_path


# --- Fleet ---


@dataclass(frozen=True)
class WorkerRow:
    """One line of the fleet table, with the numbers already worked out."""

    entry: FleetWorker
    # Bytes of the storage cap in use, and that as a percentage (bar width).
    used_bytes: int
    used_percent: int
    # Short "what is it doing" texts, one per active lease.
    lease_texts: list[str]
    # The worker's latest events, newest first; None when they could not be
    # loaded (the row says so instead of the whole page failing).
    recent_events: list[EventLine] | None
    # What its version can render (LIVESTACK-50), e.g. "Sabe fazer: vertical
    # e girar, sem áudio"; None for the studio, which renders nothing.
    render_features: str | None = None


_ROLE_ORDER = {WorkerRole.MASTER: 0, WorkerRole.WORKER: 1, WorkerRole.STUDIO: 2}


def active_workers(fleet: FleetResponse) -> list[FleetWorker]:
    """The fleet without revoked workers (LIVESTACK-42 item 9).

    A revoked worker no longer works for us, so the Frota page leaves it out;
    Admin → Workers lists it under "Revogados". Its name is still known
    (fleet.workers keeps it) so old events can say who did what.
    """
    return [entry for entry in fleet.workers if entry.worker.revoked_at is None]


def render_features_text(worker: Worker) -> str:
    """The render features a worker declared, in words; a name we have no words for stays as is.

    None declared means a version before 0.4.0: it gets only renders that
    need nothing new (horizontal 16:9 videos with sound).
    """
    features = worker.capabilities.render_features
    if not features:
        return text("fleet.render_features.none")
    names = [
        text(f"render_feature.{name}") if has_text(f"render_feature.{name}") else name
        for name in features
    ]
    return text("fleet.render_features", features=", ".join(names))


def build_worker_rows(
    fleet: FleetResponse, events_by_worker: Mapping[str, list[Event] | None] | None = None
) -> list[WorkerRow]:
    """Master first, then the other workers, then the studio; online before offline.
    Revoked workers are left out (active_workers).

    events_by_worker maps a worker id to its recent events (any order), or
    to None when loading them failed. A worker missing from it counts as
    "could not be loaded" too.
    """
    events_by_worker = events_by_worker or {}
    # Event actors are "worker:<id>"; the fleet itself knows every name.
    worker_names = {entry.worker.id: entry.worker.name for entry in fleet.workers}
    rows = []
    for entry in active_workers(fleet):
        worker = entry.worker
        cap = worker.storage_cap_bytes
        used = max(0, cap - worker.free_bytes)
        leases = [
            text(
                "fleet.lease",
                task=text(f"task.doing.{lease.task_kind.value}"),
                job=short_id(lease.job_id),
            )
            for lease in entry.leases
        ]
        events = events_by_worker.get(worker.id)
        recent = None if events is None else event_lines_newest_first(events, worker_names)
        features = None if worker.role == WorkerRole.STUDIO else render_features_text(worker)
        rows.append(WorkerRow(entry, used, percent(used, cap), leases, recent, features))
    return sorted(
        rows,
        key=lambda row: (
            _ROLE_ORDER[row.entry.worker.role],
            not row.entry.online,
            row.entry.worker.name.lower(),
        ),
    )


def jobs_by_state_in_order(fleet: FleetResponse) -> list[tuple[str, int]]:
    """Job counts in pipeline order; states this UI does not know go last."""
    known = [state.value for state in STATES_IN_PIPELINE_ORDER]
    ordered = [(state, fleet.jobs_by_state.get(state, 0)) for state in known]
    extra = [(state, count) for state, count in fleet.jobs_by_state.items() if state not in known]
    return ordered + sorted(extra)


def online_count(fleet: FleetResponse) -> int:
    return sum(1 for entry in active_workers(fleet) if entry.online)
