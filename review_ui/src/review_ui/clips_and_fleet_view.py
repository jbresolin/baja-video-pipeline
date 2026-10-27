"""Shaping data for the clips page (manifest) and the fleet page."""

from dataclasses import dataclass

from review_ui.formatting import percent
from review_ui.queue_view import STATES_IN_PIPELINE_ORDER, same_prova
from shared.models import FleetResponse, FleetWorker, ManifestEntry, WorkerRole

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


_ROLE_ORDER = {WorkerRole.MASTER: 0, WorkerRole.WORKER: 1, WorkerRole.STUDIO: 2}


def build_worker_rows(fleet: FleetResponse) -> list[WorkerRow]:
    """Master first, then the other workers, then the studio; online before offline."""
    rows = []
    for entry in fleet.workers:
        worker = entry.worker
        cap = worker.storage_cap_bytes
        used = max(0, cap - worker.free_bytes)
        leases = [f"{lease.task_kind.value} · {lease.job_id[:8]}" for lease in entry.leases]
        rows.append(WorkerRow(entry, used, percent(used, cap), leases))
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
    return sum(1 for entry in fleet.workers if entry.online)
