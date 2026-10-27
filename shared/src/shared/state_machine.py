"""Which job state changes are allowed (design §4).

The orchestrator must call `validate_transition` before every state change,
so an impossible change (e.g. received → done) fails loudly instead of
silently corrupting a job.
"""

from shared.models.enums import JobState

# For each state, the states a job may move to next, with the reason.
ALLOWED_TRANSITIONS: dict[JobState, frozenset[JobState]] = {
    JobState.RECEIVED: frozenset(
        {
            # A holder uploaded the proxy.
            JobState.PROXY_READY,
            # A holder reported the raw unusable (corrupt file, ffprobe or
            # ffmpeg error) under download_raw or make_proxy.
            JobState.FAILED,
        }
    ),
    JobState.PROXY_READY: frozenset(
        {
            # The orchestrator puts the job in the review queue.
            JobState.AWAITING_REVIEW,
            # Same as from received: a holder reported an unrecoverable error.
            JobState.FAILED,
        }
    ),
    JobState.AWAITING_REVIEW: frozenset(
        {
            # A reviewer saved the cut and fields with action "ready".
            JobState.READY,
            # A reviewer dropped a junk or accidental video.
            JobState.DISCARDED,
        }
    ),
    JobState.READY: frozenset(
        {
            # A holder claimed processing under a lease.
            JobState.CLAIMED,
            # A reviewer dropped it before anyone processed it.
            JobState.DISCARDED,
        }
    ),
    JobState.CLAIMED: frozenset(
        {
            # The clip was uploaded to the outbox, or pushed to the studio.
            JobState.UPLOADED,
            # The lease expired or was revoked, or a reviewer changed the cut:
            # the job goes back to the queue to be (re)processed.
            JobState.READY,
            # The worker reported an unrecoverable error (POST /jobs/{id}/failed).
            JobState.FAILED,
        }
    ),
    JobState.FAILED: frozenset(
        {
            # A reviewer chose "retry": the job goes back to where it failed
            # (Job.failed_from), see retry_target_for.
            JobState.RECEIVED,
            JobState.PROXY_READY,
            JobState.READY,
            # A reviewer chose "discard".
            JobState.DISCARDED,
        }
    ),
    # Final: a discarded job never comes back.
    JobState.DISCARDED: frozenset(),
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

# States from which a reviewer's "discard" action is accepted (design §4).
DISCARDABLE_STATES: frozenset[JobState] = frozenset(
    {JobState.AWAITING_REVIEW, JobState.READY, JobState.FAILED}
)

# States from which a reviewer's "retry" action is accepted.
RETRYABLE_STATES: frozenset[JobState] = frozenset({JobState.FAILED})

# States a worker failure can come from (design §4). Job.failed_from is one of them.
FAILABLE_STATES: frozenset[JobState] = frozenset(
    {JobState.RECEIVED, JobState.PROXY_READY, JobState.CLAIMED}
)


def retry_target_for(failed_from: JobState) -> JobState:
    """Where "retry" sends a failed job, given the state it failed from.

    received and proxy_ready go straight back, so the holders are offered
    the download/proxy task again. claimed goes back to ready, not claimed:
    a claimed job without a worker's lease would be stuck, and ready is the
    queue from which a holder claims it again.
    """
    if failed_from not in FAILABLE_STATES:
        raise ValueError(f"a job cannot have failed from '{failed_from.value}'")
    if failed_from == JobState.CLAIMED:
        return JobState.READY
    return failed_from


class IllegalTransitionError(ValueError):
    """Raised when code tries to move a job to a state it may not reach."""

    def __init__(self, current: JobState, requested: JobState) -> None:
        self.current = current
        self.requested = requested
        allowed = sorted(state.value for state in ALLOWED_TRANSITIONS[current])
        allowed_text = ", ".join(allowed) if allowed else "nothing (it is a final state)"
        super().__init__(
            f"job cannot go from '{current.value}' to '{requested.value}'; "
            f"from '{current.value}' it may only go to: {allowed_text}"
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
