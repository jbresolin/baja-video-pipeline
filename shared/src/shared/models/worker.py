"""Workers (master, volunteers, studio) and the tokens used to enroll them."""

from datetime import datetime

from pydantic import Field

from shared.models.common import ApiModel, ByteCount
from shared.models.enums import WorkerRole


class WorkerCapabilities(ApiModel):
    """What the worker's machine can do. Reported at enrollment and on change."""

    # True when ffmpeg lists h264_nvenc AND a one-frame test encode worked.
    has_nvenc: bool
    # The encoder the worker will actually use: "h264_nvenc" or "libx264".
    video_encoder: str
    ffmpeg_version: str | None = None
    os: str | None = None
    cpu_count: int | None = Field(default=None, ge=1)
    gpu_name: str | None = None


class WorkerStats(ApiModel):
    """Rolling averages the orchestrator computes from worker measurements.

    None means "not measured yet": there is no calibration phase (design §5),
    so a new worker starts with no numbers and gets them from its first jobs.
    """

    download_mbps: float | None = Field(default=None, ge=0)
    # Seconds of processing per gigabyte of raw input (lower is faster).
    process_s_per_gb: float | None = Field(default=None, ge=0)
    upload_mbps: float | None = Field(default=None, ge=0)


class Worker(ApiModel):
    """A machine running the worker app, as the orchestrator stores it."""

    id: str
    name: str
    role: WorkerRole
    capabilities: WorkerCapabilities
    # Disk the worker may still use under its storage cap, in bytes.
    free_bytes: ByteCount = 0
    # The volunteer-chosen limit for the working folder (design §9).
    storage_cap_bytes: ByteCount = 0
    stats: WorkerStats = Field(default_factory=WorkerStats)
    # Tailscale IP or MagicDNS name, for peer raw fetches and studio pushes.
    tailnet_address: str | None = None
    # Worker-hosted file server for peer raw fetches (default 8710).
    file_server_port: int | None = Field(default=None, ge=1, le=65535)
    # Studio only: the clip receiver for direct pushes (default 8720).
    receiver_port: int | None = Field(default=None, ge=1, le=65535)
    enrolled_at: datetime
    last_heartbeat: datetime | None = None


class EnrollmentToken(ApiModel):
    """A one-time token an admin gives a volunteer to join the fleet.

    The orchestrator stores only a hash of the token; the plaintext is shown
    once, in the response to POST /admin/enrollment-tokens.
    """

    token_sha256: str
    volunteer_name: str
    role: WorkerRole
    created_at: datetime
    expires_at: datetime
    used_at: datetime | None = None
    used_by_worker_id: str | None = None
