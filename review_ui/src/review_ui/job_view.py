"""What the job page says about a job: actions, status strip, intake, frame rate.

Everything here is computed from the Job record alone (plus worker names
from the fleet, when available), so it is plain functions that are easy to
test without a server. The event log lines live in event_log_view.py.
"""

from dataclasses import dataclass

from review_ui.event_log_view import worker_label
from shared.models import Job, JobState, ReceivedAs, ReviewAction, TaskKind
from shared.state_machine import DISCARDABLE_STATES, REPROCESSABLE_STATES, RETRYABLE_STATES

# One sentence per state: what is going on and what happens next.
STATE_EXPLANATIONS: dict[JobState, str] = {
    JobState.RECEIVED: "Raw received. Waiting for a holder to download it and render the proxy.",
    JobState.PROXY_READY: "Proxy uploaded; the job is entering the review queue.",
    JobState.AWAITING_REVIEW: "Waiting for review: set the cut and the fields, then Ready.",
    JobState.READY: "Reviewed. Waiting for a holder to claim the processing.",
    JobState.CLAIMED: "A holder is rendering the clip.",
    JobState.UPLOADED: "Clip rendered and sent. Waiting for the studio to confirm it.",
    JobState.DELIVERED: "The studio confirmed the clip; cleanup is finishing.",
    JobState.DONE: "Done: the clip is on the studio box.",
    JobState.FAILED: "A worker could not process this job. Choose Retry or Discard.",
    JobState.DISCARDED: "Discarded. Nothing more will happen to this job.",
}

# How a task reads in a sentence: "<worker> is <doing>".
TASK_DESCRIPTIONS: dict[TaskKind, str] = {
    TaskKind.DOWNLOAD_RAW: "downloading the raw",
    TaskKind.MAKE_PROXY: "rendering the proxy",
    TaskKind.PROCESS: "rendering the clip",
    TaskKind.DELIVER: "delivering the clip to the studio",
}

RECEIVED_AS_LABELS: dict[ReceivedAs, str] = {
    ReceivedAs.DOCUMENT: "a WhatsApp document",
    ReceivedAs.VIDEO: "a WhatsApp video (recompressed)",
    ReceivedAs.TEST_UPLOAD: "a test upload",
}

# Phone footage is about 30 fps; used when the job has no frame rate yet.
DEFAULT_FRAME_RATE = 30.0

# Button text for each action, in the order the buttons appear.
ACTION_LABELS: dict[ReviewAction, str] = {
    ReviewAction.SAVE: "Save",
    ReviewAction.READY: "Ready",
    ReviewAction.REPROCESS: "Reprocess",
    ReviewAction.RETRY: "Retry",
    ReviewAction.DISCARD: "Discard",
}


@dataclass(frozen=True)
class StatusNote:
    # "info", "success", "warning" or "error": picks the colour in the strip.
    level: str
    text: str


def available_actions(state: JobState) -> list[ReviewAction]:
    """The review actions the orchestrator accepts in this state (docs/api.md §7.1)."""
    actions = []
    if state != JobState.DISCARDED:
        actions.append(ReviewAction.SAVE)
    if state == JobState.AWAITING_REVIEW:
        actions.append(ReviewAction.READY)
    if state in REPROCESSABLE_STATES:
        actions.append(ReviewAction.REPROCESS)
    if state in RETRYABLE_STATES:
        actions.append(ReviewAction.RETRY)
    if state in DISCARDABLE_STATES:
        actions.append(ReviewAction.DISCARD)
    return actions


def _state_level(state: JobState) -> str:
    if state == JobState.FAILED:
        return "error"
    if state == JobState.DONE:
        return "success"
    return "info"


def explain_status(job: Job, worker_names: dict[str, str]) -> list[StatusNote]:
    """The sentences of the status strip, most important first."""
    notes = [StatusNote(_state_level(job.state), STATE_EXPLANATIONS[job.state])]

    if job.waiting_reason:
        # The orchestrator's own words for why the job is stuck.
        notes.append(StatusNote("warning", job.waiting_reason))

    if job.failure:
        failure = job.failure
        exit_text = f" (exit code {failure.exit_code})" if failure.exit_code is not None else ""
        who = worker_label(failure.worker_id, worker_names)
        doing = TASK_DESCRIPTIONS[failure.task_kind]
        notes.append(StatusNote("error", f"{who} failed {doing}{exit_text}: {failure.message}"))

    if job.low_res:
        notes.append(
            StatusNote(
                "warning",
                "Arrived as a WhatsApp video, so it is recompressed (lower quality). "
                "The bot asked the sender to resend it as a document.",
            )
        )

    proxy_expected = job.state not in {JobState.RECEIVED, JobState.DISCARDED}
    if proxy_expected and not job.proxy_available:
        notes.append(
            StatusNote(
                "warning",
                "The proxy is not on the VPS (evicted to free disk). "
                "Use “Request proxy” to have a holder render it again.",
            )
        )

    confirmed = [h for h in job.holders if h.confirmed_at is not None]
    if job.state != JobState.DISCARDED and not confirmed:
        notes.append(StatusNote("info", "No worker has a verified copy of the raw yet."))

    for lease in job.leases:
        who = worker_label(lease.worker_id, worker_names)
        notes.append(StatusNote("info", f"{who} is {TASK_DESCRIPTIONS[lease.task_kind]}."))

    if job.clip and job.clip.delivered_at:
        notes.append(StatusNote("success", f"On the studio box at {job.clip.relative_path}."))
    return notes


def intake_text(job: Job) -> str:
    """Who sent the raw and how: "Bruno as a WhatsApp document (VID_0042.mp4)"."""
    sender = job.source.sender_name or "an unknown sender"
    received_as = RECEIVED_AS_LABELS[job.source.received_as]
    filename = f" ({job.source.original_filename})" if job.source.original_filename else ""
    return f"{sender} as {received_as}{filename}"


def frame_rate_for_stepping(job: Job) -> float:
    """Frames per second the player steps by (one frame = 1 / this).

    Job.frame_rate is the proxy's rate, set when a holder uploads it. Older
    jobs, and jobs whose proxy is not uploaded yet, have none; phone footage
    is about 30 fps, so that is the fallback (design §17).
    """
    return job.frame_rate or DEFAULT_FRAME_RATE
