"""Workers (master, volunteers, studio) and the tokens used to enroll them."""

from datetime import datetime

from pydantic import Field

from shared.models.common import ByteCount, ReadByClients
from shared.models.enums import WorkerRole


class WorkerCapabilities(ReadByClients):
    """What the worker's machine can do. Reported at enrollment and on change.

    Read by clients: unknown fields are ignored. The orchestrator also receives
    it (POST /enroll); clients reading Worker.capabilities win.
    """

    # True when ffmpeg lists h264_nvenc AND a one-frame test encode worked.
    has_nvenc: bool
    # The encoder the worker will actually use: "h264_nvenc" or "libx264".
    video_encoder: str
    ffmpeg_version: str | None = None
    os: str | None = None
    cpu_count: int | None = Field(default=None, ge=1)
    gpu_name: str | None = None


class WorkerStats(ReadByClients):
    """Rolling averages the orchestrator computes from worker measurements.

    None means "not measured yet": there is no calibration phase (design §5),
    so a new worker starts with no numbers and gets them from its first jobs.

    Read by clients: unknown fields are ignored.
    """

    download_mbps: float | None = Field(default=None, ge=0)
    # Seconds of processing per gigabyte of raw input (lower is faster).
    process_s_per_gb: float | None = Field(default=None, ge=0)
    upload_mbps: float | None = Field(default=None, ge=0)


class Worker(ReadByClients):
    """A machine running the worker app, as the orchestrator stores it.

    Read by clients: unknown fields are ignored.
    """

    id: str
    # The display name every page, event line and the fleet show. Starts as
    # what the volunteer typed on the token form (prefilled with the PC's
    # host name); an admin may change it on Admin → Workers (LIVESTACK-42).
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
    # The PC's own host name, as reported at enrollment. Shown beside the
    # display name as secondary text; never changed by the orchestrator.
    hostname: str | None = None
    enrolled_at: datetime
    last_heartbeat: datetime | None = None
    # Set when an admin revoked the worker: its bearer token stopped working.
    revoked_at: datetime | None = None
    # "Rede compartilhada": the worker stays on its own tailnet (its last
    # heartbeat said so, LIVESTACK-41 item 4). Peers and the studio cannot
    # reach it: never a second holder, a peer source or a direct pusher.
    shared_tailnet: bool = False


class EnrollmentToken(ReadByClients):
    """A one-time token an admin gives a volunteer to join the fleet.

    The orchestrator stores only a hash of the token; the plaintext is shown
    once, in the response to POST /admin/enrollment-tokens.

    Read by clients: unknown fields are ignored.
    """

    token_sha256: str
    volunteer_name: str
    role: WorkerRole
    created_at: datetime
    expires_at: datetime
    used_at: datetime | None = None
    used_by_worker_id: str | None = None
    revoked_at: datetime | None = None
