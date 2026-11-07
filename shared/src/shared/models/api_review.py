"""Request/response bodies for the review UI endpoints (reviewer auth)."""

from datetime import datetime

from pydantic import Field, model_validator

from shared.models.common import (
    ByteCount,
    CarNumber,
    CropPosition,
    PassNumber,
    ReadByClients,
    ReceivedByServer,
    Rotation,
    Seconds,
)
from shared.models.enums import (
    Framing,
    JobState,
    OriginalState,
    ReviewAction,
    SenderMessageKind,
    SenderMessageRole,
)
from shared.models.job import JobSummary, check_cut_order


class ReviewQueueResponse(ReadByClients):
    """GET /review/queue: in filming order, oldest recording first
    (Job.recorded_at, ties by arrival; LIVESTACK-45).

    Read by clients: unknown fields are ignored.
    """

    items: list[JobSummary]


class CarVideosResponse(ReadByClients):
    """GET /review/car-videos: every job of one car in one prova (LIVESTACK-45).

    Discarded jobs are left out. In filming order (Job.recorded_at, ties by
    arrival), so the review UI can place a video among the car's other
    passes and suggest its passagem.

    Read by clients: unknown fields are ignored.
    """

    # The prova's clip folder (Prova.slug). Jobs are matched by it, not by
    # name, so a renamed prova still finds every video. None when the prova
    # asked for has no letters or digits (no folder; no jobs then).
    prova_slug: str | None
    car_number: CarNumber
    items: list[JobSummary]


class SenderMessage(ReadByClients):
    """One WhatsApp message from the video's sender, as the job page lists it (LIVESTACK-49).

    Read by clients: unknown fields are ignored.
    """

    # WhatsApp message id (Evolution data.key.id); the video's own for the caption.
    message_id: str
    role: SenderMessageRole
    kind: SenderMessageKind
    # The text, or the caption of an image or document. None for media
    # without a caption (audio, sticker, ...) and for a deleted message.
    text: str | None = None
    # When the bot received it (the webhook's arrival).
    received_at: datetime
    # The sender changed the text after sending it; `text` is the new one.
    edited: bool = False
    # The sender deleted it for everyone ("apagar para todos"); `text` is gone.
    deleted: bool = False


class SenderMessagesResponse(ReadByClients):
    """GET /jobs/{job_id}/sender-messages: the job page's "Mensagens de <remetente>".

    `items` in display order: the caption, then the replies to this video,
    then the messages received after it (before the sender's next video),
    then those received before it (after the sender's previous video);
    oldest first within each role.

    Read by clients: unknown fields are ignored.
    """

    job_id: str
    # The sender's name as the job has it (IntakeSource.sender_name).
    sender_name: str | None = None
    items: list[SenderMessage]


class ReviewUpdate(ReceivedByServer):
    """PATCH /jobs/{id}/review.

    Fields left out (None) keep their current value. With action "ready" or
    "reprocess", the job must end up with prova, car_number, passagem and
    both cuts set, otherwise the answer is 422 missing_review_fields.

    Received by the orchestrator: unknown fields are refused.
    """

    # The Job.version the reviewer was looking at; 409 version_conflict if
    # somebody else saved in between.
    expected_version: int = Field(ge=1)
    action: ReviewAction = ReviewAction.SAVE
    prova: str | None = Field(default=None, min_length=1, max_length=64)
    car_number: CarNumber | None = None
    passagem: PassNumber | None = None
    show_pass: bool | None = None
    cut_in_s: Seconds | None = None
    cut_out_s: Seconds | None = None
    # Render options (LIVESTACK-50): "Girar", "Enquadramento" and its band,
    # "Sem áudio". Like the fields above, left out = keep.
    rotation: Rotation | None = None
    framing: Framing | None = None
    crop_position: CropPosition | None = None
    no_audio: bool | None = None
    # "Mostrar resultado da passagem" (LIVESTACK-47). Left out = keep. A job
    # never set follows its prova's `show_by_default` (Job.show_pass_result).
    show_pass_result: bool | None = None

    @model_validator(mode="after")
    def _cuts_in_order(self) -> "ReviewUpdate":
        check_cut_order(self.cut_in_s, self.cut_out_s)
        return self


class RegenerateProxyResponse(ReadByClients):
    """Read by clients: unknown fields are ignored."""

    job_id: str
    # True when a make_proxy task is now waiting for a holder.
    queued: bool
    # Why it could not be queued right away, e.g. no holder is online.
    waiting_reason: str | None = None


class Team(ReadByClients):
    """GET /teams/{car_number}: the team of a car (teams table, synced from the results API).

    Read by clients: unknown fields are ignored.
    """

    car_number: CarNumber
    team_name: str
    school: str | None = None
    # The API's short name ("FEI" for "Equipe FEI Baja"), used on the clip overlay.
    short_name: str | None = None
    # The school's short name ("UFMG"), shown in the overlay's role pill.
    school_short: str | None = None
    # GET /teams/{car} only (LIVESTACK-42): the team and school as a clip
    # shows them, under the Settings' name choices (full or short, each
    # falling back to the other). None for a school the table does not know.
    clip_team_name: str | None = None
    clip_school_name: str | None = None


class ReviewQueueFilter(ReceivedByServer):
    """Query parameters of GET /review/queue, as a model for validation.

    Received by the orchestrator: unknown fields are refused.
    """

    states: list[JobState] = Field(default_factory=lambda: [JobState.AWAITING_REVIEW])
    limit: int = Field(default=100, ge=1, le=500)


class OriginalDownload(ReadByClients):
    """GET/POST /jobs/{job_id}/original: the job page's "Baixar original" (item 15).

    Read by clients: unknown fields are ignored.
    """

    state: OriginalState
    # Relative to the public base URL, e.g.
    # "/original-downloads/<job_id>/joao-…-2f2510a3.mp4"; only when "ready".
    download_url: str | None = None
    # The name the download gets (the raw's readable name, item 12).
    file_name: str | None = None
    size_bytes: ByteCount | None = None
    # When the VPS deletes its copy unless someone asks again (24 h after
    # the last request, admin Settings); None unless "ready".
    expires_at: datetime | None = None
