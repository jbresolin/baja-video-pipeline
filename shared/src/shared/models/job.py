"""The Job: one pass video, from WhatsApp intake to a clip on the studio box."""

from datetime import datetime
from typing import Any

from pydantic import Field, model_validator

from shared.models.common import (
    ByteCount,
    CarNumber,
    PassNumber,
    ReadByClients,
    Seconds,
    Sha256Hex,
    utc_now,
)
from shared.models.enums import (
    DeliveryRoute,
    HolderStatus,
    JobState,
    ReceivedAs,
    RecordedAtSource,
    TaskKind,
)
from shared.models.render_inputs import ClipInputsComparison, RenderInputs


def check_cut_order(cut_in_s: float | None, cut_out_s: float | None) -> None:
    """Raise ValueError when both cuts are set and the out point is not after the in point."""
    if cut_in_s is not None and cut_out_s is not None and cut_out_s <= cut_in_s:
        raise ValueError(f"cut_out_s ({cut_out_s}) must be greater than cut_in_s ({cut_in_s})")


class Lease(ReadByClients):
    """Permission for one worker to do one task on one job until expires_at.

    Heartbeats (or /lease/renew) push expires_at forward. When it passes, the
    orchestrator drops the lease and the task can be handed to someone else.

    Read by clients: unknown fields are ignored.
    """

    lease_id: str
    job_id: str
    worker_id: str
    task_kind: TaskKind
    acquired_at: datetime
    expires_at: datetime


class Holder(ReadByClients):
    """A worker that has (or is fetching) a verified copy of the job's raw.

    Read by clients: unknown fields are ignored.
    """

    worker_id: str
    status: HolderStatus
    assigned_at: datetime
    confirmed_at: datetime | None = None


class IntakeSource(ReadByClients):
    """Where the raw came from. Kept for support and duplicate detection.

    Read by clients: unknown fields are ignored.
    """

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
    # The raw's readable name on workers and for downloads (LIVESTACK-41
    # item 12), e.g. "joao-guilherme-bresolin-20261104-232909-2f2510a3.mp4":
    # sender, when the bot received it (São Paulo time), start of the job id.
    # None for jobs from before.
    raw_file_name: str | None = None
    # When the filmmaker sent it (WhatsApp messageTimestamp) and when the bot
    # got the webhook (LIVESTACK-45). None for jobs from before (sent_at) and
    # for messages without a timestamp.
    sent_at: datetime | None = None
    received_at: datetime | None = None


class ClipInfo(ReadByClients):
    """The finished, rendered clip (the current version).

    Read by clients: unknown fields are ignored.
    """

    sha256: Sha256Hex
    size_bytes: ByteCount
    # Path on the studio box relative to its clip root, e.g. "enduro/42/3.mp4".
    relative_path: str
    route: DeliveryRoute
    rendered_by_worker_id: str
    delivered_at: datetime | None = None
    # 1 for the first rendering, 2 after a reprocess, ... (ClipVersion).
    version: int = Field(default=1, ge=1)
    # True while the VPS outbox still has a copy (studio offline, or not yet
    # archived by the master): the review UI offers it for download at
    # download_url ("/clip-downloads/<job_id>.mp4", relative to the public
    # base URL, reviewer auth like proxies). None when not in the outbox.
    in_outbox: bool = False
    download_url: str | None = None
    # The name a download should get, e.g. "enduro-042-p3.mp4" or
    # "enduro-042-p3-v2.mp4" (shared.layout, LIVESTACK-41 item 12).
    download_file_name: str | None = None


class ClipVersion(ReadByClients):
    """One rendering of a job's clip. Reprocessing adds a version; none is replaced.

    Read by clients: unknown fields are ignored.
    """

    version: int = Field(ge=1)
    sha256: Sha256Hex
    size_bytes: ByteCount
    # Box path at render time, e.g. "enduro/42/3.mp4".
    relative_path: str
    # Path in the master's archive folder: "enduro/42/3.mp4", "enduro/42/3.v2.mp4".
    archive_relative_path: str
    rendered_by_worker_id: str | None = None
    rendered_at: datetime
    # When the master confirmed its archive copy (sha256 checked); None until then.
    archived_at: datetime | None = None
    # What this version was rendered from (LIVESTACK-43); None for clips
    # rendered before the orchestrator recorded it.
    render_inputs: RenderInputs | None = None


class ProbeReport(ReadByClients):
    """PUT /jobs/{id}/media-info body, stored as Job.media_info.

    The raw's full ffprobe answer (`-show_format -show_streams`, see
    shared.media_probe.build_probe_command), as the first holder read it
    while making the proxy, minus location tags: shared.media_details.
    strip_location_tags removes every GPS/location tag and the worker
    (and again the orchestrator) only keeps `location_present`. The inner
    dicts are ffprobe's own and are kept as they are (any keys).

    Read by clients: unknown fields are ignored. The orchestrator also receives
    it (PUT /jobs/{id}/media-info); clients reading Job.media_info win.
    """

    # ffprobe's "format" object: container, duration, size, bit_rate, tags.
    format: dict[str, Any] = Field(default_factory=dict)
    # ffprobe's "streams" list: one object per video/audio/data stream.
    streams: list[dict[str, Any]] = Field(default_factory=list)
    # True when the file had GPS/location tags (removed before sending).
    location_present: bool = False


class JobFailure(ReadByClients):
    """The last unrecoverable error a worker reported (POST /jobs/{id}/failed).

    Read by clients: unknown fields are ignored.
    """

    worker_id: str
    task_kind: TaskKind
    # The worker's own words, e.g. the last lines of ffmpeg's error output.
    message: str
    exit_code: int | None = None
    failed_at: datetime


class Job(ReadByClients):
    """Full job record, as returned by GET /jobs/{id}.

    Read by clients: unknown fields are ignored.
    """

    id: str
    state: JobState
    # The review version: goes up by one only when a reviewer changes the job
    # (any PATCH /review action). Machine activity (holders, proxy, leases)
    # never bumps it. PATCH /review must send the version it saw, so two
    # reviewers cannot silently overwrite each other (docs/api.md §4).
    version: int = Field(default=1, ge=1)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    source: IntakeSource
    # True when the file arrived as a recompressed WhatsApp video (design §3).
    low_res: bool = False
    # When the video was filmed (LIVESTACK-45): the raw's creation_time when
    # plausible (not after the bot received it, not more than 7 days
    # before), else the WhatsApp send time, else the time received. The
    # review queue and the "other videos of this car" panel sort by it.
    # `recorded_at_source` says which: only "metadata" is exact. None only
    # from an orchestrator older than this.
    recorded_at: datetime | None = None
    recorded_at_source: RecordedAtSource | None = None

    # --- The raw file ---
    sha256: Sha256Hex
    size_bytes: ByteCount
    # Filled in by the first holder (ffprobe) when it confirms the download.
    duration_s: Seconds | None = None
    has_audio: bool | None = None
    # The raw's ffprobe report (container, codecs, device tags), sent by the
    # first holder with the proxy (PUT /jobs/{id}/media-info). None until then.
    media_info: ProbeReport | None = None
    # Frames per second of the proxy (= the raw's, the proxy keeps a constant
    # frame rate). Reported with the proxy upload; the review UI steps one
    # frame with it and falls back to 30 while it is None.
    frame_rate: float | None = Field(default=None, gt=0)
    # True while the VPS inbox still has a copy (until two holders confirm,
    # or the job is discarded).
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
    # Team name from the teams table (results API) by car number, copied at review.
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
    # Every rendering ever made, oldest first, with its archive status.
    clip_versions: list[ClipVersion] = Field(default_factory=list)
    # The newest clip version's render inputs next to what a render would
    # use now (LIVESTACK-43). `changed` not empty = the clip is out of date
    # and only a reprocess fixes it. None without a clip version, for a
    # version rendered before inputs were recorded, and for a discarded job.
    clip_inputs: ClipInputsComparison | None = None
    # Set when a worker reported a failure; shown to the reviewer, who then
    # chooses retry or discard. Cleared by "retry".
    failure: JobFailure | None = None
    # While state is "failed": the state the job failed from (received,
    # proxy_ready or claimed). "retry" returns it there (claimed → ready).
    # None in every other state.
    failed_from: JobState | None = None
    # While state is "discarded": the state it was discarded from
    # (awaiting_review, ready or failed). "restore" returns it there.
    discarded_from: JobState | None = None
    # While state is "discarded": until when holders keep the raw (and the
    # VPS its inbox copy). Restore always works before this; after it, only
    # while a holder still has the raw (RAW_UNAVAILABLE otherwise).
    discard_grace_ends_at: datetime | None = None
    # Human-readable reason the job is stuck, shown in the review UI,
    # e.g. "waiting for a holder to come back online". None when not stuck.
    waiting_reason: str | None = None

    @model_validator(mode="after")
    def _cuts_in_order(self) -> "Job":
        check_cut_order(self.cut_in_s, self.cut_out_s)
        return self


class JobSummary(ReadByClients):
    """The slice of a Job shown in lists (review queue, fleet view).

    Read by clients: unknown fields are ignored.
    """

    id: str
    state: JobState
    version: int
    created_at: datetime
    low_res: bool
    sender_name: str | None
    # When it was filmed, and how sure that is (Job.recorded_at). None only
    # from an orchestrator older than LIVESTACK-45.
    recorded_at: datetime | None = None
    recorded_at_source: RecordedAtSource | None = None
    size_bytes: ByteCount
    duration_s: Seconds | None
    proxy_available: bool
    proxy_url: str | None
    thumbnail_url: str | None
    prova: str | None
    car_number: CarNumber | None
    passagem: PassNumber | None
    waiting_reason: str | None
    # The worker's error message when state is "failed", else None.
    failure_message: str | None
    # Where a reviewer can download the clip while the VPS outbox has it
    # (ClipInfo.download_url), else None.
    clip_download_url: str | None = None
    # True when the newest clip no longer matches the review (Job.clip_inputs).
    clip_outdated: bool = False

    @classmethod
    def from_job(cls, job: Job) -> "JobSummary":
        return cls(
            id=job.id,
            state=job.state,
            version=job.version,
            created_at=job.created_at,
            low_res=job.low_res,
            sender_name=job.source.sender_name,
            recorded_at=job.recorded_at,
            recorded_at_source=job.recorded_at_source,
            size_bytes=job.size_bytes,
            duration_s=job.duration_s,
            proxy_available=job.proxy_available,
            proxy_url=job.proxy_url,
            thumbnail_url=job.thumbnail_url,
            prova=job.prova,
            car_number=job.car_number,
            passagem=job.passagem,
            waiting_reason=job.waiting_reason,
            failure_message=job.failure.message if job.failure else None,
            clip_download_url=job.clip.download_url if job.clip else None,
            clip_outdated=bool(job.clip_inputs and job.clip_inputs.changed),
        )
