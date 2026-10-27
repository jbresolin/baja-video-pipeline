"""Turning the orchestrator's event log into lines people can read.

Used by the job page (one job's whole history, GET /jobs/{id}/events) and
the fleet page (each worker's latest events, GET /events?worker_id=…).
Plain functions over shared.models.Event, so they are easy to test.
"""

from dataclasses import dataclass
from datetime import datetime

from review_ui.formatting import short_id, state_label
from shared.models import Event

# Wording for the common event kinds (shared/models/events.py lists them).
# A kind not listed here still shows up, as its name with spaces.
KIND_TEXTS: dict[str, str] = {
    "lease_acquired": "started a task",
    "lease_ended": "finished a task",
    "lease_revoked": "task taken away",
    "lease_expired": "task timed out (no heartbeat)",
    "holder_confirmed": "confirmed its copy of the raw",
    "holder_rejected": "copy of the raw rejected",
    "holder_lost": "no longer holds the raw",
    "task_failed": "task failed",
    "proxy_requested": "proxy requested",
    "worker_enrolled": "worker enrolled",
    "enrollment_token_created": "enrollment token created",
}

# Who "evolution" is: the WhatsApp gateway that delivers the raws.
EVOLUTION_LABEL = "WhatsApp"


@dataclass(frozen=True)
class EventLine:
    """One event, with every part already in words."""

    at: datetime
    # Who did it: a worker name, a reviewer's user name, "system", ...
    actor: str
    # What happened: "awaiting review → ready", "task failed", ...
    what: str
    # The orchestrator's own words (e.g. a failure message), or None.
    reason: str | None
    # The worker the event is about, when that is not the actor itself
    # (e.g. the system expiring a worker's lease). None otherwise.
    about_worker: str | None
    # The job the event is about, for the fleet page's links. None for
    # events about a worker only (e.g. worker_enrolled).
    job_id: str | None


def worker_label(worker_id: str, worker_names: dict[str, str]) -> str:
    """The worker's name when the fleet told us, else a short id."""
    return worker_names.get(worker_id) or f"worker {short_id(worker_id)}"


def actor_label(actor: str, worker_names: dict[str, str]) -> str:
    """Event.actor in words: "worker:<id>" → its name, "reviewer:ana" → "ana"."""
    kind, _, identity = actor.partition(":")
    if kind == "worker" and identity:
        return worker_label(identity, worker_names)
    if kind == "reviewer" and identity:
        return identity
    if actor == "evolution":
        return EVOLUTION_LABEL
    # "system", "admin", or a kind of actor this UI does not know yet.
    return actor


def describe_kind(event: Event) -> str:
    """What happened, in words. State changes show both states."""
    if event.kind == "state_changed" and event.to_state is not None:
        if event.from_state is None:
            # The very first state of a new job.
            return f"created as {state_label(event.to_state)}"
        return f"{state_label(event.from_state)} → {state_label(event.to_state)}"
    return KIND_TEXTS.get(event.kind, event.kind.replace("_", " "))


def describe_event(event: Event, worker_names: dict[str, str]) -> EventLine:
    about_worker = None
    # Name the worker only when the actor is someone else; "maria-notebook:
    # task failed · maria-notebook" would say the same name twice.
    if event.worker_id and event.actor != f"worker:{event.worker_id}":
        about_worker = worker_label(event.worker_id, worker_names)
    return EventLine(
        at=event.at,
        actor=actor_label(event.actor, worker_names),
        what=describe_kind(event),
        reason=event.reason,
        about_worker=about_worker,
        job_id=event.job_id,
    )


def event_lines_newest_first(events: list[Event], worker_names: dict[str, str]) -> list[EventLine]:
    """The API sends events oldest first; pages show the newest on top.

    Sorted by id rather than reversed blindly: ids only ever grow, so this
    is the true order even if two events share the same timestamp.
    """
    ordered = sorted(events, key=lambda event: event.id, reverse=True)
    return [describe_event(event, worker_names) for event in ordered]
