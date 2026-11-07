"""Which workers may render which jobs: render features (LIVESTACK-50).

A worker version that learns to render something new declares it as a
*render feature* (shared.models.RenderFeature): in its capabilities at
enrollment and in every heartbeat. A process task lists the features its
job needs, and the orchestrator offers the render only to a worker that
declared all of them. A job that needs nothing new goes to any worker, as
before. Without this, a 0.3.0 worker would take a vertical video and render
the old narrow clip with its sound, ignoring task fields it does not know.

To add a feature (LIVESTACK-47 will, for pass results in the overlay):

1. add it to RenderFeature and to the list the worker declares
   (worker/render_features.py);
2. say here, in required_render_features(), when a job needs it;
3. bump the installer version; deploy the VPS first, then the workers.

Feature names travel as plain strings, so a worker may declare one this
orchestrator has never heard of; it is simply never required.
"""

import json
import sqlite3
from datetime import datetime

from orchestrator.domain.fleet_snapshot import FleetSnapshot
from orchestrator.domain.review_queue import NO_HOLDER_ONLINE
from orchestrator.repositories import leases as leases_repo
from shared.framing import needs_framing
from shared.models import HolderStatus, RenderFeature

# Job.waiting_reason when holders are online but none runs a version that
# can render the job's options. The review UI shows it in pt-BR.
NO_HOLDER_CAN_RENDER = (
    "waiting for a holder of the raw that can render these options"
    " (a worker updated to 0.4.0 or later)"
)


def required_render_features(job_row: sqlite3.Row) -> list[str]:
    """The features a render of this job needs now, in a fixed order.

    framing:  the reviewer turned the picture ("Girar"), or the picture is
              not 16:9 (an upright phone, 4:3, ultra-wide);
    no_audio: "Sem áudio" on a raw that has sound (a raw without sound
              already renders silent everywhere).
    """
    needed = []
    if needs_framing(job_row["display_width"], job_row["display_height"], job_row["rotation"]):
        needed.append(RenderFeature.FRAMING.value)
    if job_row["no_audio"] and job_row["has_audio"] != 0:
        needed.append(RenderFeature.NO_AUDIO.value)
    return needed


def worker_render_features(worker_row: sqlite3.Row) -> frozenset[str]:
    """What the worker declared (WorkerCapabilities.render_features); empty = none."""
    capabilities = json.loads(worker_row["capabilities_json"] or "{}")
    return frozenset(capabilities.get("render_features") or [])


def can_render(worker_row: sqlite3.Row, required: list[str]) -> bool:
    return set(required) <= worker_render_features(worker_row)


def store_render_features(
    connection: sqlite3.Connection, worker_row: sqlite3.Row, features: list[str] | None
) -> None:
    """What a heartbeat declared replaces the stored list; left out = none.

    "Left out = none" matters when a PC goes back to an older installer:
    its old worker sends no list, and must not keep the new one's features.
    """
    capabilities = json.loads(worker_row["capabilities_json"] or "{}")
    declared = sorted(set(features or []))
    if capabilities.get("render_features", []) == declared:
        return
    capabilities["render_features"] = declared
    connection.execute(
        "UPDATE workers SET capabilities_json = ? WHERE id = ?",
        (json.dumps(capabilities), worker_row["id"]),
    )


def process_waiting_reason(
    connection: sqlite3.Connection, job_row: sqlite3.Row, now: datetime
) -> str | None:
    """Why a ready job's render is not starting, or None when a holder can take it.

    No confirmed holder online (and not paused) → NO_HOLDER_ONLINE; holders
    online but none declared the features the job needs → NO_HOLDER_CAN_RENDER.
    """
    snapshot = FleetSnapshot.take(connection, now)
    available = [
        snapshot.workers_by_id[holder.worker_id]
        for holder in leases_repo.list_holders(connection, job_row["id"])
        if holder.status == HolderStatus.CONFIRMED
        and snapshot.is_available_for_work(holder.worker_id)
    ]
    if not available:
        return NO_HOLDER_ONLINE
    required = required_render_features(job_row)
    if not any(can_render(worker_row, required) for worker_row in available):
        return NO_HOLDER_CAN_RENDER
    return None
