"""The orchestrator's event log: GET /jobs/{id}/events and GET /events (design §17).

Every state change, lease, holder change and failure is appended to one
log. The review UI shows a job's history from it and the fleet page shows
what each worker did recently.
"""

from datetime import datetime
from typing import Any

from pydantic import Field

from shared.models.common import ReadByClients
from shared.models.enums import JobState


class Event(ReadByClients):
    """One line of the log: who did what, to which job or worker, when and why.

    Read by clients: unknown fields are ignored.
    """

    # Increases with every event; a stable order and a cursor for clients.
    id: int = Field(ge=1)
    at: datetime
    job_id: str | None = None
    worker_id: str | None = None
    # "system", "worker:<id>", "reviewer:<username>", "admin:<username>",
    # "admin" (the static admin token) or "evolution".
    actor: str
    # What happened. Not a closed list; the common ones are state_changed,
    # lease_acquired, lease_ended, lease_revoked, lease_expired,
    # holder_confirmed, holder_rejected, holder_lost, task_failed,
    # proxy_requested, worker_enrolled, enrollment_token_created, and the
    # admin changes listed under `details`.
    kind: str
    # Set for state_changed only.
    from_state: JobState | None = None
    to_state: JobState | None = None
    # Human words, e.g. "proxy uploaded" or the worker's failure message.
    reason: str | None = None
    # Admin changes (kinds user_*, contact_*, prova_*, team*, settings_*,
    # enrollment_token_revoked, worker_revoked): {"before": ..., "after": ...},
    # each the changed record as JSON (None when it did not exist).
    # result_hold_started (LIVESTACK-62): {"until": <ISO time>, "waits_for":
    # ["result", "clock"]}, the deadline and what the render waits for.
    details: dict[str, Any] | None = None


class EventsResponse(ReadByClients):
    """Read by clients: unknown fields are ignored."""

    # Oldest first.
    events: list[Event]
