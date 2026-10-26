"""The Job: one pass video, from WhatsApp intake to a clip on the studio box."""

from datetime import datetime

from pydantic import Field, model_validator

from shared.models.common import (
    ApiModel,
    ByteCount,
    CarNumber,
    PassNumber,
    Seconds,
    Sha256Hex,
    utc_now,
)
from shared.models.enums import (
    DeliveryRoute,
    HolderStatus,
    JobState,
    ReceivedAs,
    TaskKind,
)


def check_cut_order(cut_in_s: float | None, cut_out_s: float | None) -> None:
    """Raise ValueError when both cuts are set and the out point is not after the in point."""
    if cut_in_s is not None and cut_out_s is not None and cut_out_s <= cut_in_s:
        raise ValueError(f"cut_out_s ({cut_out_s}) must be greater than cut_in_s ({cut_in_s})")


class Lease(ApiModel):
    """Permission for one worker to do one task on one job until expires_at.

    Heartbeats (or /lease/renew) push expires_at forward. When it passes, the
    orchestrator drops the lease and the task can be handed to someone else.
    """

    lease_id: str
    job_id: str
    worker_id: str
    task_kind: TaskKind
    acquired_at: datetime
    expires_at: datetime


class Holder(ApiModel):
    """A worker that has (or is fetching) a verified copy of the job's raw."""

    worker_id: str
    status: HolderStatus
    assigned_at: datetime
    confirmed_at: datetime | None = None


class IntakeSource(ApiModel):
    """Where the raw came from. Kept for support and duplicate detection."""

    # WhatsApp message id (Evolution data.key.id). Used to ignore retries of
    # the same webhook. Test uploads get a generated id.
    message_id: str
    # Sender's WhatsApp id, e.g. "5511999999999@s.whatsapp.net".
    sender_jid: str | None = None
    # Sender's display name in WhatsApp (Evolution data.pushName).
    sender_name: str | None = None
    received_as: ReceivedAs
    original_filename: str | None = None
    mimetype: str | None = None


class ClipInfo(ApiModel):
    """The finished, rendered clip."""

    sha256: Sha256Hex
    size_bytes: ByteCount
    # Path on the studio box relative to its clip root, e.g. "enduro/42/3.mp4".
    relative_path: str
    route: DeliveryRoute
    rendered_by_worker_id: str
    delivered_at: datetime | None = None


class Job(ApiModel):
    """Full job record, as returned by GET /jobs/{id}."""

    id: str
    state: JobState
    # Goes up by one on every change. PATCH /review must send the version it
    # saw, so two reviewers cannot silently overwrite each other.
    version: int = Field(default=1, ge=1)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    source: IntakeSource
    # True when the file arrived as a recompressed WhatsApp video (design §3).
    low_res: bool = False

    # --- The raw file ---
    sha256: Sha256Hex
    size_bytes: ByteCount
    # Filled in by the first holder (ffprobe) when it confirms the download.
    duration_s: Seconds | None = None
    has_audio: bool | None = None
    # True while the VPS inbox still has a copy (until two holders confirm).
    raw_on_vps: bool = True
    holders: list[Holder] = Field(default_factory=list)
    # Active leases on this job (several download_raw leases can coexist).
    leases: list[Lease] = Field(default_factory=list)

    # --- Proxy for review (served statically by Caddy) ---
    proxy_available: bool = False
    proxy_url: str | None = None
    thumbnail_url: str | None = None

    # --- Review fields (design §6) ---
    prova: str | None = None
    car_number: CarNumber | None = None
    # Team name resolved from the Baja app by car number, copied at review.
    team_name: str | None = None
    passagem: PassNumber | None = None
    # Whether the overlay shows the pass number.
    show_pass: bool = True
    cut_in_s: Seconds | None = None
    cut_out_s: Seconds | None = None
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None

    # --- Output ---
    clip: ClipInfo | None = None
    # Human-readable reason the job is stuck, shown in the review UI,
    # e.g. "waiting for a holder to come back online". None when not stuck.
    waiting_reason: str | None = None

    @model_validator(mode="after")
    def _cuts_in_order(self) -> "Job":
        check_cut_order(self.cut_in_s, self.cut_out_s)
        return self


class JobSummary(ApiModel):
    """The slice of a Job shown in lists (review queue, fleet view)."""

    id: str
    state: JobState
    version: int
    created_at: datetime
    low_res: bool
    sender_name: str | None
    size_bytes: ByteCount
    duration_s: Seconds | None
    proxy_available: bool
    proxy_url: str | None
    thumbnail_url: str | None
    prova: str | None
    car_number: CarNumber | None
    passagem: PassNumber | None
    waiting_reason: str | None

    @classmethod
    def from_job(cls, job: Job) -> "JobSummary":
        return cls(
            id=job.id,
            state=job.state,
            version=job.version,
            created_at=job.created_at,
            low_res=job.low_res,
            sender_name=job.source.sender_name,
            size_bytes=job.size_bytes,
            duration_s=job.duration_s,
            proxy_available=job.proxy_available,
            proxy_url=job.proxy_url,
            thumbnail_url=job.thumbnail_url,
            prova=job.prova,
            car_number=job.car_number,
            passagem=job.passagem,
            waiting_reason=job.waiting_reason,
        )
