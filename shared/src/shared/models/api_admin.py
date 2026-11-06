"""Bodies for intake, enrollment, fleet and manifest endpoints."""

from datetime import datetime

from pydantic import Field

from shared.models.common import ApiModel, ByteCount, CarNumber, PassNumber, Sha256Hex
from shared.models.enums import WorkerRole
from shared.models.job import Lease
from shared.models.render_inputs import ClipInputsComparison
from shared.models.worker import Worker, WorkerCapabilities

# --- Intake ---


class IntakeResponse(ApiModel):
    """Answer to the Evolution webhook and to the dev test upload.

    The webhook answers fast and downloads in the background (design §15):
    202 with accepted=true and job_id=None; the job appears once the media
    is downloaded, decrypted and verified. Ignored and duplicate messages
    get 200, so Evolution does not retry them. The test upload is
    synchronous and returns the new job_id straight away.
    """

    # The new job (test upload) or the existing one (duplicate); else None.
    job_id: str | None
    # WhatsApp message id (data.key.id); None for test uploads.
    message_id: str | None = None
    # True when a background download of the media was started.
    accepted: bool = False
    ignored: bool = False
    # Why it was ignored, e.g. "not a video document" or "duplicate message".
    reason: str | None = None
    duplicate: bool = False
    low_res: bool = False


# --- Enrollment ---


class CreateEnrollmentTokenRequest(ApiModel):
    volunteer_name: str = Field(min_length=1, max_length=100)
    role: WorkerRole = WorkerRole.WORKER
    valid_for_hours: int = Field(default=72, ge=1, le=24 * 30)


class EnrollmentTokenCreated(ApiModel):
    # Shown exactly once. The orchestrator keeps only its hash.
    token: str
    # Public id for the token list and revocation (EnrollmentTokenInfo.token_id).
    token_id: str
    volunteer_name: str
    role: WorkerRole
    expires_at: datetime
    # Where the volunteer downloads the installer (admin setting); None if unset.
    installer_download_url: str | None = None


class EnrollRequest(ApiModel):
    """POST /enroll, sent once by the worker's first-run setup page."""

    enrollment_token: str
    # The display name (Worker.name): what the volunteer typed on the token
    # form, prefilled with the host name. An admin may change it later.
    worker_name: str = Field(min_length=1, max_length=100)
    # The PC's host name (Worker.hostname), shown as secondary text.
    hostname: str | None = None
    capabilities: WorkerCapabilities
    storage_cap_bytes: ByteCount


class EnrollResponse(ApiModel):
    worker_id: str
    role: WorkerRole
    # Send as "Authorization: Bearer <token>" on every later call. Store it safely.
    bearer_token: str
    # Single-use, pre-approved, tagged, non-ephemeral (design §8). Use it once
    # with "tailscale up --authkey=..."; it is useless afterwards.
    tailscale_auth_key: str
    tailscale_tags: list[str]
    # Caddy's tailnet listener, e.g. "http://100.101.102.103:8080": the base
    # of every inbox/outbox URL in task offers. The worker sends its bearer
    # token only to this origin and to its own orchestrator_url (design §17).
    tailnet_base_url: str
    heartbeat_interval_s: int
    lease_ttl_s: int


# --- Fleet ---


class ServerDisk(ApiModel):
    """Disk use on the VPS (design §10)."""

    inbox_bytes: ByteCount
    # Soft limit (BVP_INBOX_HOLD_GB): above it the bot asks senders to hold.
    inbox_hold_bytes: ByteCount
    # Hard limit (BVP_INBOX_CAP_GB): above it intake refuses new media.
    inbox_cap_bytes: ByteCount
    proxy_bytes: ByteCount
    outbox_bytes: ByteCount
    free_bytes: ByteCount


class FleetWorker(ApiModel):
    worker: Worker
    online: bool
    leases: list[Lease]
    held_job_count: int = Field(ge=0)
    held_bytes: ByteCount
    # False near the storage cap: no new raws, still processes held ones.
    accepting_new_raws: bool
    # The volunteer paused it (tray icon / status page): no new tasks, still
    # serves held raws to peers. Shown as "pausado" on the fleet page.
    paused: bool = False
    # Finished clips the worker keeps, and how many of those the master
    # has archived (only those may be evicted). The master's own archive is
    # FleetResponse.clip_archive.
    clips_held: int = Field(default=0, ge=0)
    clips_archived: int = Field(default=0, ge=0)


class ClipArchiveProgress(ApiModel):
    """How much of every clip ever rendered (all versions) the master has."""

    archived: int = Field(ge=0)
    total: int = Field(ge=0)


class FleetResponse(ApiModel):
    workers: list[FleetWorker]
    server_disk: ServerDisk
    # Count of jobs per state, e.g. {"awaiting_review": 4, "ready": 2}.
    jobs_by_state: dict[str, int]
    # "N of M clips archived" on the master.
    clip_archive: ClipArchiveProgress = Field(
        default_factory=lambda: ClipArchiveProgress(archived=0, total=0)
    )


# --- Manifest (read by the studio and the later playout UI, LIVESTACK-8) ---


class ManifestEntry(ApiModel):
    job_id: str
    prova: str
    car_number: CarNumber
    team_name: str | None
    passagem: PassNumber
    # Relative to the clip root on the studio box: "<prova>/<car>/<passagem>.mp4".
    relative_path: str
    sha256: Sha256Hex
    size_bytes: ByteCount
    delivered_at: datetime
    # Which rendering of this pass it is: 1, then 2 after a reprocess, ...
    # The master archives every version (<passagem>.mp4, <passagem>.v2.mp4);
    # the box and this manifest only have the current one.
    version: int = Field(default=1, ge=1)
    # True while the VPS outbox still has this clip: the review UI's clips
    # page offers it for download (reviewer auth, Caddy /outbox-download/).
    in_outbox: bool = False
    download_url: str | None = None
    # The team and school as the clip shows them (Settings' name choices,
    # LIVESTACK-42); team_name above stays the full name.
    clip_team_name: str | None = None
    clip_school_name: str | None = None
    # This clip's render inputs next to the review now (LIVESTACK-43), as
    # Job.clip_inputs: `changed` not empty = out of date. None when unknown.
    clip_inputs: ClipInputsComparison | None = None


class ManifestResponse(ApiModel):
    # Newest delivery first.
    clips: list[ManifestEntry]
    generated_at: datetime
