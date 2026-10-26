"""Tests for shared.state_machine: which job state changes are allowed.

A job moves through states like "received" and "ready" (see design §4).
The state machine is the single list of allowed moves. These tests check
every allowed move works and that a selection of nonsense moves fail with
a clear error.
"""

import pytest

from shared.models import JobState
from shared.state_machine import (
    ALLOWED_TRANSITIONS,
    REPROCESSABLE_STATES,
    IllegalTransitionError,
    is_transition_allowed,
    validate_transition,
)

# Every legal (from, to) pair, written out by hand from design §4 on
# purpose: if someone edits ALLOWED_TRANSITIONS by mistake, this list
# will disagree and the tests below will catch it.
LEGAL_TRANSITIONS = [
    # The happy path, one step at a time.
    (JobState.RECEIVED, JobState.PROXY_READY),
    (JobState.PROXY_READY, JobState.AWAITING_REVIEW),
    (JobState.AWAITING_REVIEW, JobState.READY),
    (JobState.READY, JobState.CLAIMED),
    (JobState.CLAIMED, JobState.UPLOADED),
    (JobState.UPLOADED, JobState.DELIVERED),
    (JobState.DELIVERED, JobState.DONE),
    # Lease expired / revoked: back in the queue.
    (JobState.CLAIMED, JobState.READY),
    # Reviewer changed the cut after processing started or finished.
    (JobState.UPLOADED, JobState.READY),
    (JobState.DELIVERED, JobState.READY),
    (JobState.DONE, JobState.READY),
]


@pytest.mark.parametrize(("current", "requested"), LEGAL_TRANSITIONS)
def test_every_legal_transition_is_accepted(current, requested):
    # "parametrize" runs this one test once per pair in LEGAL_TRANSITIONS.
    # validate_transition returns nothing when the move is fine and raises
    # an exception when it is not, so simply reaching the assert below
    # (no exception) already proves the move was accepted.
    validate_transition(current, requested)
    assert is_transition_allowed(current, requested)


def test_the_hand_written_list_matches_the_code_exactly():
    # Build the set of pairs the code allows, and compare with our list.
    # If the code allowed an extra move that the design does not mention,
    # this comparison would show it.
    pairs_in_code = {
        (current, requested)
        for current, targets in ALLOWED_TRANSITIONS.items()
        for requested in targets
    }
    assert pairs_in_code == set(LEGAL_TRANSITIONS)


def test_every_state_has_an_entry_in_the_table():
    # If a new state were added to JobState but forgotten in the table,
    # looking it up would crash at runtime. This catches that early.
    assert set(ALLOWED_TRANSITIONS) == set(JobState)


ILLEGAL_TRANSITIONS = [
    # Skipping the review entirely.
    (JobState.RECEIVED, JobState.READY),
    # Jumping from intake straight to the end.
    (JobState.RECEIVED, JobState.DONE),
    # Processing without a proxy or a review.
    (JobState.RECEIVED, JobState.CLAIMED),
    # Delivering a clip that was never rendered.
    (JobState.READY, JobState.DELIVERED),
    # Going backwards to before the proxy existed.
    (JobState.DONE, JobState.RECEIVED),
    (JobState.AWAITING_REVIEW, JobState.RECEIVED),
    # Finishing a job whose clip was never uploaded nor acknowledged by the studio.
    (JobState.CLAIMED, JobState.DONE),
    # "Moving" to the state the job is already in.
    (JobState.READY, JobState.READY),
]


@pytest.mark.parametrize(("current", "requested"), ILLEGAL_TRANSITIONS)
def test_illegal_transitions_raise_a_clear_error(current, requested):
    # pytest.raises(...) is a "with" block that passes only if the code
    # inside raises that exception type. If no exception happened, the
    # test fails, which is exactly what we want for an illegal move.
    with pytest.raises(IllegalTransitionError) as caught:
        validate_transition(current, requested)

    # The error must carry both states, so the orchestrator can log them
    # and turn them into a 409 illegal_transition answer...
    assert caught.value.current == current
    assert caught.value.requested == requested
    # ...and its message must name the states, so a human reading a log
    # understands what was attempted.
    message = str(caught.value)
    assert current.value in message
    assert requested.value in message
    assert is_transition_allowed(current, requested) is False


def test_error_message_lists_what_would_have_been_allowed():
    # From "claimed" a job may go to "ready" or "uploaded". When someone
    # tries something else, the message should suggest those two.
    with pytest.raises(IllegalTransitionError) as caught:
        validate_transition(JobState.CLAIMED, JobState.DONE)
    assert "ready" in str(caught.value)
    assert "uploaded" in str(caught.value)


def test_reprocessable_states_can_all_go_back_to_ready():
    # The review endpoint uses REPROCESSABLE_STATES to decide whether
    # action="reprocess" is allowed. Every one of those states must have a
    # legal move to "ready", otherwise the endpoint would accept a request
    # and then crash on the state change.
    for state in REPROCESSABLE_STATES:
        assert is_transition_allowed(state, JobState.READY), state
