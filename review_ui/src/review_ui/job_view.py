"""What the job page says about a job: actions, status strip, intake, frame rate.

Everything here is computed from the Job record alone (plus worker names
from the fleet, when available), so it is plain functions that are easy to
test without a server. The event log lines live in event_log_view.py.
Every sentence comes from the pt-BR catalog (review_ui/catalog/).
"""

import re
from dataclasses import dataclass
from datetime import datetime

from review_ui.catalog import text
from review_ui.delivery_view import delivery_notes
from review_ui.event_log_view import worker_label
from review_ui.formatting import clock_text, state_label
from shared.models import Job, JobState, ReceivedAs, ReviewAction, TaskKind, utc_now
from shared.state_machine import (
    DISCARDABLE_STATES,
    REPROCESSABLE_STATES,
    RESTORABLE_STATES,
    RETRYABLE_STATES,
)

# One sentence per state: what is going on and what happens next. Built from
# the catalog for every state, so a state without a sentence fails at import.
STATE_EXPLANATIONS: dict[JobState, str] = {
    state: text(f"explain.{state.value}") for state in JobState
}

# How a task reads in a sentence: "<worker> está <doing>." and "falhou ao <verb>".
TASK_DESCRIPTIONS: dict[TaskKind, str] = {
    kind: text(f"task.doing.{kind.value}") for kind in TaskKind
}
TASK_VERBS: dict[TaskKind, str] = {kind: text(f"task.verb.{kind.value}") for kind in TaskKind}

RECEIVED_AS_LABELS: dict[ReceivedAs, str] = {
    received_as: text(f"received_as.{received_as.value}") for received_as in ReceivedAs
}

# Phone footage is about 30 fps; used when the job has no frame rate yet.
DEFAULT_FRAME_RATE = 30.0

# Button text for each action, in the order the buttons appear.
ACTION_LABELS: dict[ReviewAction, str] = {
    action: text(f"action.{action.value}") for action in ReviewAction
}

# Job.waiting_reason is an English sentence written by the orchestrator
# (domain/review_queue.py and domain/task_results.py). The two kinds it
# writes are put in pt-BR here; any other reason is shown as it came, marked
# as English. A test keeps the first one equal to the orchestrator's.
NO_HOLDER_ONLINE_REASON = "waiting for a holder of the raw to come back online (or be resumed)"
_TASK_FAILED_ON = re.compile(
    r"^(?P<kind>[a-z_]+) failed on (?P<worker>.+?): (?P<message>.*)$", re.DOTALL
)


@dataclass(frozen=True)
class StatusNote:
    # "info", "success", "warning" or "error": picks the colour in the strip.
    level: str
    text: str
    # Shown after the text, as a São Paulo time (delivery notes).
    at: datetime | None = None
    # The orchestrator's or a worker's own words (English, e.g. an ffmpeg
    # error), shown after the text and marked lang="en". None when there are none.
    detail: str | None = None


@dataclass(frozen=True)
class WaitingReason:
    """Job.waiting_reason for people: pt-BR text, plus the English part we cannot translate."""

    text: str
    detail: str | None


def describe_waiting_reason(reason: str) -> WaitingReason:
    """Put the orchestrator's waiting reason in pt-BR when it is one we know."""
    if reason == NO_HOLDER_ONLINE_REASON:
        return WaitingReason(text("waiting.no_holder_online"), None)
    match = _TASK_FAILED_ON.match(reason)
    if match and match["kind"] in {kind.value for kind in TaskKind}:
        task = TASK_VERBS[TaskKind(match["kind"])]
        return WaitingReason(
            text("waiting.task_failed_on", task=task, worker=match["worker"]), match["message"]
        )
    return WaitingReason("", reason)


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
    if state in RESTORABLE_STATES:
        actions.append(ReviewAction.RESTORE)
    return actions


@dataclass(frozen=True)
class ActionButton:
    value: str
    label: str
    # The one button that moves the job forward (Ctrl+Enter): drawn first
    # and in the accent colour.
    primary: bool


# The "move it forward" actions; a state offers at most one of them.
PRIMARY_ACTIONS = (ReviewAction.READY, ReviewAction.REPROCESS, ReviewAction.RETRY)


def action_buttons(state: JobState) -> list[ActionButton]:
    """The job page's buttons, in order (LIVESTACK-43).

    Once a clip exists (claimed, uploaded, delivered, done), the obvious
    action after an edit is to render it again: "Salvar e reprocessar"
    (the reprocess action, which saves the fields in the same request)
    comes first as the primary button, and plain "Salvar" follows as a
    secondary one. Elsewhere "Salvar" stays first.
    """
    actions = available_actions(state)
    if ReviewAction.REPROCESS in actions:
        actions.remove(ReviewAction.REPROCESS)
        actions.insert(0, ReviewAction.REPROCESS)
    return [
        ActionButton(action.value, ACTION_LABELS[action], action in PRIMARY_ACTIONS)
        for action in actions
    ]


def save_message(job: Job) -> str:
    """What the page says after a plain save.

    On a job whose clip is made or being made, saving changes nothing on
    the studio box: only "Salvar e reprocessar" does, and the message says so.
    """
    if job.state in REPROCESSABLE_STATES:
        return text("action_done.save_clip_unchanged")
    return text("action_done.save")


def _state_level(state: JobState) -> str:
    if state == JobState.FAILED:
        return "error"
    if state == JobState.DONE:
        return "success"
    return "info"


def discard_note(job: Job, now: datetime) -> StatusNote | None:
    """What "Restaurar" will do for a discarded job: within or past the grace period."""
    if job.state != JobState.DISCARDED:
        return None
    back_to = state_label(job.discarded_from or JobState.AWAITING_REVIEW)
    ends_at = job.discard_grace_ends_at
    if ends_at is not None and now < ends_at:
        return StatusNote(
            "info", text("explain.discard_grace", until=clock_text(ends_at), state=back_to)
        )
    return StatusNote("warning", text("explain.discard_grace_over"))


def explain_status(
    job: Job, worker_names: dict[str, str], now: datetime | None = None
) -> list[StatusNote]:
    """The sentences of the status strip, most important first."""
    notes = [StatusNote(_state_level(job.state), STATE_EXPLANATIONS[job.state])]
    discarded = discard_note(job, now or utc_now())
    if discarded is not None:
        notes.append(discarded)

    if job.waiting_reason:
        # Why the job is stuck, in the orchestrator's words when we do not know them.
        waiting = describe_waiting_reason(job.waiting_reason)
        notes.append(StatusNote("warning", waiting.text, detail=waiting.detail))

    if job.failure:
        failure = job.failure
        exit_code = (
            text("explain.exit_code", code=failure.exit_code)
            if failure.exit_code is not None
            else ""
        )
        who = worker_label(failure.worker_id, worker_names)
        task = TASK_VERBS[failure.task_kind]
        sentence = text("explain.task_failed", who=who, task=task, exit_code=exit_code)
        notes.append(StatusNote("error", sentence, detail=failure.message))

    if job.low_res:
        notes.append(StatusNote("warning", text("explain.low_res")))

    proxy_expected = job.state not in {JobState.RECEIVED, JobState.DISCARDED}
    if proxy_expected and not job.proxy_available:
        notes.append(StatusNote("warning", text("explain.proxy_evicted")))

    confirmed = [h for h in job.holders if h.confirmed_at is not None]
    if job.state != JobState.DISCARDED and not confirmed:
        notes.append(StatusNote("info", text("explain.no_verified_copy")))

    for lease in job.leases:
        who = worker_label(lease.worker_id, worker_names)
        doing = TASK_DESCRIPTIONS[lease.task_kind]
        notes.append(StatusNote("info", text("explain.task_running", who=who, doing=doing)))

    # Where the clip is: studio, outbox, master archive (delivery_view.py).
    for note in delivery_notes(job):
        notes.append(StatusNote(note.level, note.text, note.at))
    return notes


def intake_text(job: Job) -> str:
    """Who sent the raw and how: "Bruno, como documento do WhatsApp (VID_0042.mp4)"."""
    sender = job.source.sender_name or text("intake.unknown_sender")
    received_as = RECEIVED_AS_LABELS[job.source.received_as]
    if job.source.original_filename:
        return text(
            "intake.with_filename",
            sender=sender,
            received_as=received_as,
            filename=job.source.original_filename,
        )
    return text("intake.text", sender=sender, received_as=received_as)


def frame_rate_for_stepping(job: Job) -> float:
    """Frames per second the player steps by (one frame = 1 / this).

    Job.frame_rate is the proxy's rate, set when a holder uploads it. Older
    jobs, and jobs whose proxy is not uploaded yet, have none; phone footage
    is about 30 fps, so that is the fallback (design §17).
    """
    return job.frame_rate or DEFAULT_FRAME_RATE
