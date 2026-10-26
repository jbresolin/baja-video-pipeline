"""Bodies for intake, enrollment, fleet and manifest endpoints."""

from datetime import datetime

from pydantic import Field

from shared.models.common import ApiModel, ByteCount, CarNumber, PassNumber, Sha256Hex
from shared.models.enums import WorkerRole
from shared.models.job import Lease
from shared.models.worker import Worker, WorkerCapabilities

# --- Intake ---


class IntakeResponse(ApiModel):
    """Answer to the Evolution webhook and to the dev test upload.

    Always 200 for messages we understood, even when ignored, so Evolution
    does not retry them forever.
    """

    # None when the message was ignored (not a video, or a duplicate).
    job_id: str | None
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
    volunteer_name: str
    role: WorkerRole
    expires_at: datetime


class EnrollRequest(ApiModel):
    """POST /enroll, sent once by the worker's first-run setup page."""

    enrollment_token: str
    worker_name: str = Field(min_length=1, max_length=100)
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
    heartbeat_interval_s: int
    lease_ttl_s: int


# --- Fleet ---


class ServerDisk(ApiModel):
    """Disk use on the VPS (design §10)."""

    inbox_bytes: ByteCount
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


class FleetResponse(ApiModel):
    workers: list[FleetWorker]
    server_disk: ServerDisk
    # Count of jobs per state, e.g. {"awaiting_review": 4, "ready": 2}.
    jobs_by_state: dict[str, int]


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


class ManifestResponse(ApiModel):
    # Newest delivery first.
    clips: list[ManifestEntry]
    generated_at: datetime
