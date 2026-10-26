"""Which job state changes are allowed (design §4).

The orchestrator must call `validate_transition` before every state change,
so an impossible change (e.g. received → done) fails loudly instead of
silently corrupting a job.
"""

from shared.models.enums import JobState

# For each state, the states a job may move to next, with the reason.
ALLOWED_TRANSITIONS: dict[JobState, frozenset[JobState]] = {
    # A holder uploaded the proxy.
    JobState.RECEIVED: frozenset({JobState.PROXY_READY}),
    # The orchestrator puts the job in the review queue.
    JobState.PROXY_READY: frozenset({JobState.AWAITING_REVIEW}),
    # A reviewer saved the cut and fields with action "ready".
    JobState.AWAITING_REVIEW: frozenset({JobState.READY}),
    # A holder claimed processing under a lease.
    JobState.READY: frozenset({JobState.CLAIMED}),
    JobState.CLAIMED: frozenset(
        {
            # The clip was uploaded to the outbox, or pushed to the studio.
            JobState.UPLOADED,
            # The lease expired or was revoked, or a reviewer changed the cut:
            # the job goes back to the queue to be (re)processed.
            JobState.READY,
        }
    ),
    JobState.UPLOADED: frozenset(
        {
            # The studio acknowledged a verified copy.
            JobState.DELIVERED,
            # A reviewer changed the cut before delivery finished.
            JobState.READY,
        }
    ),
    JobState.DELIVERED: frozenset(
        {
            # Cleanup finished (outbox copy deleted).
            JobState.DONE,
            # Reprocessing requested after delivery; the new clip replaces the old.
            JobState.READY,
        }
    ),
    # Reprocessing requested on a finished job.
    JobState.DONE: frozenset({JobState.READY}),
}

# States from which a reviewer's "reprocess" action is accepted.
REPROCESSABLE_STATES: frozenset[JobState] = frozenset(
    {JobState.CLAIMED, JobState.UPLOADED, JobState.DELIVERED, JobState.DONE}
)


class IllegalTransitionError(ValueError):
    """Raised when code tries to move a job to a state it may not reach."""

    def __init__(self, current: JobState, requested: JobState) -> None:
        self.current = current
        self.requested = requested
        allowed = sorted(state.value for state in ALLOWED_TRANSITIONS[current])
        super().__init__(
            f"job cannot go from '{current.value}' to '{requested.value}'; "
            f"from '{current.value}' it may only go to: {', '.join(allowed)}"
        )


def is_transition_allowed(current: JobState, requested: JobState) -> bool:
    return requested in ALLOWED_TRANSITIONS[current]


def validate_transition(current: JobState, requested: JobState) -> None:
    """Do nothing if current → requested is allowed, else raise IllegalTransitionError.

    Staying in the same state is not a transition and is rejected too: code
    that "moves" a job to where it already is usually has a bug.
    """
    if not is_transition_allowed(current, requested):
        raise IllegalTransitionError(current, requested)
