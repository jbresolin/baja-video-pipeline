"""Turning the orchestrator's event log into lines people can read.

Used by the job page (one job's whole history, GET /jobs/{id}/events) and
the fleet page (each worker's latest events, GET /events?worker_id=…).
Plain functions over shared.models.Event, so they are easy to test.

The event kinds stored in the DB stay English; only how they are shown is
pt-BR (catalog keys "event.<kind>"). The reason is the orchestrator's own
words and is shown as it came.
"""

from dataclasses import dataclass
from datetime import datetime

from review_ui.catalog import has_text, text
from review_ui.formatting import short_id, state_label
from shared.models import Event


@dataclass(frozen=True)
class EventLine:
    """One event, with every part already in words."""

    at: datetime
    # Who did it: a worker name, a reviewer's user name, "sistema", ...
    actor: str
    # What happened: "aguardando revisão → pronto", "tarefa falhou", ...
    what: str
    # True when `what` is a kind this UI has no words for (shown as code).
    what_is_kind_name: bool
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
    return worker_names.get(worker_id) or text("actor.worker_id", id=short_id(worker_id))


def actor_label(actor: str, worker_names: dict[str, str]) -> str:
    """Event.actor in words: "worker:<id>" → its name, "reviewer:ana" → "ana"."""
    kind, _, identity = actor.partition(":")
    if kind == "worker" and identity:
        return worker_label(identity, worker_names)
    if kind in ("reviewer", "admin") and identity:
        return identity
    if actor == "admin":
        # The static admin token, not a person.
        return text("actor.admin_token")
    if actor == "evolution":
        # The WhatsApp gateway that delivers the raws.
        return text("actor.evolution")
    if actor == "system":
        return text("actor.system")
    # A kind of actor this UI does not know yet.
    return actor


def kind_text(kind: str) -> str | None:
    """An event kind in words, or None when the catalog has none for it."""
    key = f"event.{kind}"
    return text(key) if has_text(key) else None


def describe_kind(event: Event) -> tuple[str, bool]:
    """What happened, in words, and whether that is just the kind's name."""
    if event.kind == "state_changed" and event.to_state is not None:
        if event.from_state is None:
            # The very first state of a new job.
            return text("events.created_as", state=state_label(event.to_state)), False
        return f"{state_label(event.from_state)} → {state_label(event.to_state)}", False
    words = kind_text(event.kind)
    return (words, False) if words is not None else (event.kind, True)


def describe_event(event: Event, worker_names: dict[str, str]) -> EventLine:
    about_worker = None
    # Name the worker only when the actor is someone else; "maria-notebook:
    # tarefa falhou · maria-notebook" would say the same name twice.
    if event.worker_id and event.actor != f"worker:{event.worker_id}":
        about_worker = worker_label(event.worker_id, worker_names)
    what, what_is_kind_name = describe_kind(event)
    return EventLine(
        at=event.at,
        actor=actor_label(event.actor, worker_names),
        what=what,
        what_is_kind_name=what_is_kind_name,
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
