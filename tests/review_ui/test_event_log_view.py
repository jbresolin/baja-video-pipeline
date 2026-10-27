"""Tests for event_log_view: how one orchestrator Event becomes a readable line.

No web requests here. Each test builds an Event (the same pydantic model
the orchestrator sends from GET /jobs/{id}/events and GET /events) and
checks the words the job page and the fleet page would show for it.
"""

from datetime import timedelta

import pytest

from review_ui.event_log_view import (
    actor_label,
    describe_event,
    describe_kind,
    event_lines_newest_first,
)
from shared.models import Event, JobState
from tests.review_ui.sample_data import MASTER_ID, T0, VOLUNTEER_ID

# Worker id → name, as the pages get it from GET /fleet.
NAMES = {MASTER_ID: "joao-desktop"}


def event(**changes) -> Event:
    """A plain event (a system state change); keyword arguments override fields."""
    fields = {
        "id": 1,
        "at": T0,
        "actor": "system",
        "kind": "state_changed",
        "job_id": "job-1",
        "from_state": JobState.PROXY_READY,
        "to_state": JobState.AWAITING_REVIEW,
    }
    fields.update(changes)
    return Event(**fields)


# --- Who did it ---


@pytest.mark.parametrize(
    ("actor", "expected"),
    [
        # A worker the fleet knows is shown by its name...
        (f"worker:{MASTER_ID}", "joao-desktop"),
        # ...and one it does not know (or a failed fleet call) by a short id.
        (f"worker:{VOLUNTEER_ID}", "worker w-volunt"),
        # A reviewer is shown by their user name alone.
        ("reviewer:ana", "ana"),
        # "evolution" is the WhatsApp gateway; people know it as WhatsApp.
        ("evolution", "WhatsApp"),
        ("system", "system"),
        ("admin", "admin"),
        # A kind of actor added to the API later still shows, unchanged.
        ("robot:7", "robot:7"),
    ],
)
def test_actor_label(actor, expected):
    assert actor_label(actor, NAMES) == expected


# --- What happened ---


def test_state_change_shows_both_states_in_words():
    # Underscores become spaces, and an arrow joins the old and new state.
    assert describe_kind(event()) == "proxy ready → awaiting review"


def test_first_state_of_a_new_job_says_created():
    # A brand-new job has no previous state (from_state is None).
    assert describe_kind(event(from_state=None, to_state=JobState.RECEIVED)) == (
        "created as received"
    )


def test_known_kinds_get_friendly_words():
    assert describe_kind(event(kind="lease_expired", from_state=None, to_state=None)) == (
        "task timed out (no heartbeat)"
    )
    assert describe_kind(event(kind="holder_confirmed", from_state=None, to_state=None)) == (
        "confirmed its copy of the raw"
    )


def test_unknown_kind_is_still_shown_readably():
    # The list of kinds is open; a new one must not crash or vanish.
    assert describe_kind(event(kind="studio_went_fishing", from_state=None, to_state=None)) == (
        "studio went fishing"
    )


# --- Whole lines ---


def test_line_names_the_worker_when_someone_else_acted_on_it():
    # The system (not the worker) expired the master's lease: the line has
    # to say whose lease it was.
    line = describe_event(
        event(kind="lease_expired", worker_id=MASTER_ID, from_state=None, to_state=None), NAMES
    )

    assert line.actor == "system"
    assert line.about_worker == "joao-desktop"


def test_line_does_not_repeat_the_worker_that_acted_itself():
    # The master reported its own failure: its name is already the actor.
    line = describe_event(
        event(
            actor=f"worker:{MASTER_ID}",
            kind="task_failed",
            worker_id=MASTER_ID,
            from_state=None,
            to_state=None,
            reason="ffmpeg exited",
        ),
        NAMES,
    )

    assert line.actor == "joao-desktop"
    assert line.about_worker is None
    # The orchestrator's reason is passed through as-is.
    assert line.reason == "ffmpeg exited"
    assert line.job_id == "job-1"


def test_lines_are_newest_first_by_event_id():
    # Setup: three events where two share the same timestamp. The API sends
    # them oldest first; the id says which of the two came later.
    events = [
        event(id=1, at=T0, kind="first", from_state=None, to_state=None),
        event(id=2, at=T0 + timedelta(minutes=1), kind="second", from_state=None, to_state=None),
        event(id=3, at=T0 + timedelta(minutes=1), kind="third", from_state=None, to_state=None),
    ]

    lines = event_lines_newest_first(events, NAMES)

    # Newest on top; the tie between ids 2 and 3 is broken by the id.
    assert [line.what for line in lines] == ["third", "second", "first"]
