"""The orchestrator's event log: GET /jobs/{id}/events and GET /events (design §17).

Every state change, lease, holder change and failure is appended to one
log. The review UI shows a job's history from it and the fleet page shows
what each worker did recently.
"""

from datetime import datetime

from pydantic import Field

from shared.models.common import ApiModel
from shared.models.enums import JobState


class Event(ApiModel):
    """One line of the log: who did what, to which job or worker, when and why."""

    # Increases with every event; a stable order and a cursor for clients.
    id: int = Field(ge=1)
    at: datetime
    job_id: str | None = None
    worker_id: str | None = None
    # "system", "worker:<id>", "reviewer:<username>", "admin" or "evolution".
    actor: str
    # What happened. Not a closed list; the common ones are state_changed,
    # lease_acquired, lease_ended, lease_revoked, lease_expired,
    # holder_confirmed, holder_rejected, holder_lost, task_failed,
    # proxy_requested, worker_enrolled, enrollment_token_created.
    kind: str
    # Set for state_changed only.
    from_state: JobState | None = None
    to_state: JobState | None = None
    # Human words, e.g. "proxy uploaded" or the worker's failure message.
    reason: str | None = None


class EventsResponse(ApiModel):
    # Oldest first.
    events: list[Event]
