"""The job page's "Outros vídeos de <prova>, carro <n>" panel (LIVESTACK-45).

A reviewer must say whether a video is the car's 1st, 2nd or nth passagem
in the prova. The orchestrator lists the car's videos in that prova
(GET /review/car-videos, filming order, discarded ones left out); this
module places the video being reviewed among them and works out:

* the summary line, "Passagens já registradas: 1 (14:02), 2 (14:31)";
* a **suggestion**: one more than the passagem of the last numbered video
  filmed before this one (1 when none was), unless that number is already
  taken, in which case there is no suggestion, only the reason. The page
  never fills it in by itself: the reviewer clicks a button;
* **warnings** for the passagem typed in the form: the number is already
  another video's, or it breaks the filming order (a video filmed before
  passagem 2 numbered 3). They never block: re-runs and judges' decisions
  are real cases.

Everything here is plain functions over API objects, tested without a
browser (tests/review_ui/test_passagem_help_view.py). Filming order is
the orchestrator's: recording time, then arrival, then id.
"""

from dataclasses import dataclass
from datetime import datetime

from review_ui.catalog import text
from review_ui.formatting import recorded_time_text
from shared.models import Job, JobSummary, RecordedAtSource


@dataclass(frozen=True)
class PanelRow:
    """One video in the panel's list (this one included, marked)."""

    video: JobSummary
    is_this_video: bool
    # "14:02", or with the date when it is not this video's day.
    time_text: str
    # True when the time is only the WhatsApp send or receive time.
    time_is_hint: bool


@dataclass(frozen=True)
class PassagemHelp:
    prova: str
    car_number: int
    # The video being reviewed, with the passagem the form says now.
    this_video: JobSummary
    # Times on this day show without the date (this video's recording day).
    reference_time: datetime
    # This video and the others, in filming order.
    rows: list[PanelRow]
    # False when there is no other video of this car in this prova.
    has_others: bool
    # "Passagens já registradas: 1 (14:02), 2 (14:31)", or the "none yet" text.
    registered_text: str
    # The passagem to suggest (None: no suggestion) and the sentence about it.
    suggestion: int | None
    suggestion_text: str
    # For the passagem typed in the form; empty when it fits.
    warnings: list[str]
    # Some time in the list is only a send/receive time: the order may be off.
    order_is_approximate: bool


def filming_order_key(video: JobSummary) -> tuple:
    """The orchestrator's order: recording time, then arrival, then id."""
    return (video.recorded_at or video.created_at, video.created_at, video.id)


def _time_is_hint(video: JobSummary) -> bool:
    return video.recorded_at is None or video.recorded_at_source != RecordedAtSource.METADATA


def _time_text(video: JobSummary, reference: datetime) -> str:
    return recorded_time_text(video.recorded_at or video.created_at, reference)


def this_video_summary(job: Job, typed_passagem: int | None) -> JobSummary:
    """The job being reviewed as a list entry, with the passagem the form says now."""
    return JobSummary.from_job(job).model_copy(update={"passagem": typed_passagem})


def _registered_text(others: list[JobSummary], reference: datetime) -> str:
    numbered = sorted(
        (video for video in others if video.passagem is not None),
        key=lambda video: (video.passagem, filming_order_key(video)),
    )
    if not numbered:
        return text("passagens.registered_none")
    parts = [
        text(
            "passagens.registered_item", passagem=video.passagem, time=_time_text(video, reference)
        )
        for video in numbered
    ]
    return text("passagens.registered", items=", ".join(parts))


def _suggest(
    this_video: JobSummary, others: list[JobSummary], reference: datetime
) -> tuple[int | None, str]:
    """(passagem to suggest or None, the sentence that explains it)."""
    if not others:
        return 1, text("passagens.suggest_first_no_others", passagem=1)
    this_key = filming_order_key(this_video)
    numbered_before = [
        video
        for video in others
        if video.passagem is not None and filming_order_key(video) < this_key
    ]
    if numbered_before:
        last = numbered_before[-1]
        candidate = last.passagem + 1
        reason = text(
            "passagens.suggest_after",
            passagem=candidate,
            previous=last.passagem,
            time=_time_text(last, reference),
        )
    elif any(video.passagem is not None for video in others):
        candidate = 1
        reason = text("passagens.suggest_first", passagem=1)
    else:
        candidate = 1
        reason = text("passagens.suggest_first_none_numbered", passagem=1)
    taken_by = next((video for video in others if video.passagem == candidate), None)
    if taken_by is not None:
        return None, text(
            "passagens.no_suggestion_taken",
            passagem=candidate,
            time=_time_text(taken_by, reference),
        )
    return candidate, reason


def passagem_warnings(
    this_video: JobSummary, others: list[JobSummary], reference: datetime
) -> list[str]:
    """What is odd about the passagem typed for this video (none typed: nothing)."""
    passagem = this_video.passagem
    if passagem is None:
        return []
    warnings = [
        text("passagens.warning_taken", passagem=passagem, time=_time_text(video, reference))
        for video in others
        if video.passagem == passagem
    ]
    this_key = filming_order_key(this_video)
    # Filmed after this one but numbered lower: this one comes too late.
    filmed_after_lower = [
        video
        for video in others
        if video.passagem is not None
        and video.passagem < passagem
        and filming_order_key(video) > this_key
    ]
    if filmed_after_lower:
        first = filmed_after_lower[0]
        warnings.append(
            text(
                "passagens.warning_filmed_before",
                passagem=passagem,
                other=first.passagem,
                time=_time_text(first, reference),
            )
        )
    # Filmed before this one but numbered higher: this one comes too early.
    filmed_before_higher = [
        video
        for video in others
        if video.passagem is not None
        and video.passagem > passagem
        and filming_order_key(video) < this_key
    ]
    if filmed_before_higher:
        last = filmed_before_higher[-1]
        warnings.append(
            text(
                "passagens.warning_filmed_after",
                passagem=passagem,
                other=last.passagem,
                time=_time_text(last, reference),
            )
        )
    return warnings


def passagem_help(
    job: Job,
    prova: str,
    car_number: int,
    car_videos: list[JobSummary],
    typed_passagem: int | None,
) -> PassagemHelp:
    """The panel for `job`, with prova, car and passagem as the form has them now.

    `car_videos` is the orchestrator's list for that prova and car; the job
    itself may be in it (when saved with those values) and is replaced by
    its form values.
    """
    this_video = this_video_summary(job, typed_passagem)
    others = sorted((video for video in car_videos if video.id != job.id), key=filming_order_key)
    # Times on this video's day show without the date.
    reference = this_video.recorded_at or this_video.created_at
    everyone = sorted([this_video, *others], key=filming_order_key)
    rows = [
        PanelRow(
            video=video,
            is_this_video=video.id == job.id,
            time_text=_time_text(video, reference),
            time_is_hint=_time_is_hint(video),
        )
        for video in everyone
    ]
    suggestion, suggestion_text = _suggest(this_video, others, reference)
    return PassagemHelp(
        prova=prova,
        car_number=car_number,
        this_video=this_video,
        reference_time=reference,
        rows=rows,
        has_others=bool(others),
        registered_text=_registered_text(others, reference),
        suggestion=suggestion,
        suggestion_text=suggestion_text,
        warnings=passagem_warnings(this_video, others, reference),
        order_is_approximate=any(row.time_is_hint for row in rows),
    )
