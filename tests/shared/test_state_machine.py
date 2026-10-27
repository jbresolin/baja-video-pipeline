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
    DISCARDABLE_STATES,
    FAILABLE_STATES,
    REPROCESSABLE_STATES,
    RETRYABLE_STATES,
    IllegalTransitionError,
    is_transition_allowed,
    retry_target_for,
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
    # A reviewer discards a junk video, before or after marking it ready.
    (JobState.AWAITING_REVIEW, JobState.DISCARDED),
    (JobState.READY, JobState.DISCARDED),
    # A worker reports an unrecoverable error: while the raw is being
    # downloaded or the proxy rendered (received, proxy_ready), or while
    # processing (claimed)...
    (JobState.RECEIVED, JobState.FAILED),
    (JobState.PROXY_READY, JobState.FAILED),
    (JobState.CLAIMED, JobState.FAILED),
    # ...and the reviewer then retries it (back to where it failed; a job
    # that failed while claimed goes back to ready) or discards it.
    (JobState.FAILED, JobState.RECEIVED),
    (JobState.FAILED, JobState.PROXY_READY),
    (JobState.FAILED, JobState.READY),
    (JobState.FAILED, JobState.DISCARDED),
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
    # Discarded is final: no retry, no review, nothing.
    (JobState.DISCARDED, JobState.READY),
    (JobState.DISCARDED, JobState.AWAITING_REVIEW),
    (JobState.DISCARDED, JobState.FAILED),
    # Discard is only for jobs a reviewer can see (awaiting_review, ready,
    # failed); a job still without a proxy, or already processing or
    # delivered, cannot be discarded.
    (JobState.RECEIVED, JobState.DISCARDED),
    (JobState.PROXY_READY, JobState.DISCARDED),
    (JobState.CLAIMED, JobState.DISCARDED),
    (JobState.DONE, JobState.DISCARDED),
    # Only received, proxy_ready and claimed can fail; a job waiting for a
    # human, or a delivered clip, cannot.
    (JobState.AWAITING_REVIEW, JobState.FAILED),
    (JobState.READY, JobState.FAILED),
    (JobState.UPLOADED, JobState.FAILED),
    (JobState.DELIVERED, JobState.FAILED),
    # A failed job goes back through "ready"; it is never claimed directly.
    (JobState.FAILED, JobState.CLAIMED),
    (JobState.FAILED, JobState.DONE),
    # Retry never skips the review: a failed job cannot jump into the queue.
    (JobState.FAILED, JobState.AWAITING_REVIEW),
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


def test_discardable_and_retryable_states_match_the_table():
    # Same idea for the "discard" and "retry" review actions: the review
    # endpoint checks these sets first, so every state in them must have
    # the matching legal move, and no other state may have it.
    discard_sources = {s for s in JobState if is_transition_allowed(s, JobState.DISCARDED)}
    assert discard_sources == DISCARDABLE_STATES
    assert {JobState.FAILED} == RETRYABLE_STATES
    assert is_transition_allowed(JobState.FAILED, JobState.READY)


def test_discarded_is_a_final_state_and_the_error_says_so():
    # No move at all is allowed out of "discarded", and the error message
    # explains that instead of ending with an empty list.
    assert ALLOWED_TRANSITIONS[JobState.DISCARDED] == frozenset()
    with pytest.raises(IllegalTransitionError) as caught:
        validate_transition(JobState.DISCARDED, JobState.READY)
    assert "final state" in str(caught.value)


def test_failable_states_are_exactly_the_states_with_a_move_to_failed():
    # FAILABLE_STATES is the list the orchestrator checks before recording
    # Job.failed_from. It must agree with the transition table, or the
    # orchestrator could record a failure the table then refuses.
    sources_of_failed = {s for s in JobState if is_transition_allowed(s, JobState.FAILED)}
    assert sources_of_failed == FAILABLE_STATES
    assert {JobState.RECEIVED, JobState.PROXY_READY, JobState.CLAIMED} == FAILABLE_STATES


@pytest.mark.parametrize(
    ("failed_from", "expected_target"),
    [
        # A raw that could not be downloaded or turned into a proxy goes
        # back to the same step, so the holders are offered it again.
        (JobState.RECEIVED, JobState.RECEIVED),
        (JobState.PROXY_READY, JobState.PROXY_READY),
        # A job that failed while being processed goes back to the queue
        # ("ready"), not to "claimed", because nobody holds a lease on it.
        (JobState.CLAIMED, JobState.READY),
    ],
)
def test_retry_returns_the_job_to_where_it_failed(failed_from, expected_target):
    target = retry_target_for(failed_from)
    assert target == expected_target
    # And the table must allow that move, or the review endpoint would
    # accept "retry" and then crash on the state change.
    assert is_transition_allowed(JobState.FAILED, target)


def test_retry_target_refuses_a_state_a_job_cannot_fail_from():
    # A job never fails from "done", so asking where to retry it to is a
    # bug in the caller. A ValueError makes that bug loud.
    with pytest.raises(ValueError):
        retry_target_for(JobState.DONE)
