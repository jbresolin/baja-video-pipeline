"""Request/response bodies for the worker loop endpoints (bearer auth)."""

from datetime import datetime

from pydantic import Field

from shared.models.common import ApiModel, ByteCount, Seconds, Sha256Hex
from shared.models.enums import (
    FailureScope,
    JobState,
    LeaseHeartbeatStatus,
    MeasurementKind,
    WorkerRole,
)
from shared.models.job import Lease


class Measurement(ApiModel):
    """One timed transfer or render, e.g. "downloaded 500 MB in 42 s"."""

    kind: MeasurementKind
    byte_count: ByteCount
    seconds: float = Field(gt=0)


class HeartbeatRequest(ApiModel):
    """POST /workers/{id}/heartbeat, every 15 s."""

    free_bytes: ByteCount
    storage_cap_bytes: ByteCount
    tailnet_address: str | None = None
    file_server_port: int | None = Field(default=None, ge=1, le=65535)
    # Studio only: port of the clip receiver (POST /receive/{job_id}).
    receiver_port: int | None = Field(default=None, ge=1, le=65535)
    # Leases the worker is still working under. Listing one here renews it.
    current_lease_ids: list[str] = Field(default_factory=list)
    # Jobs whose raw is on this worker's disk, so the orchestrator can
    # reconcile its holder list after crashes or manual deletions.
    held_job_ids: list[str] = Field(default_factory=list)
    # Measurements finished since the previous heartbeat (may be empty).
    measurements: list[Measurement] = Field(default_factory=list)


class HeartbeatResponse(ApiModel):
    # For every lease id the worker sent: renewed, or lost (stop that task).
    lease_status: dict[str, LeaseHeartbeatStatus] = Field(default_factory=dict)
    heartbeat_interval_s: int
    # False when the worker is near its storage cap: it will not be offered
    # new download_raw tasks but keeps processing what it holds (design §9).
    accepting_new_raws: bool
    # Jobs from held_job_ids that were discarded: the worker may delete
    # its copy of their raw (design §4). Never lists a job still in use.
    deletable_job_ids: list[str] = Field(default_factory=list)


class LeaseRenewRequest(ApiModel):
    lease_id: str


class LeaseRenewResponse(ApiModel):
    lease: Lease


class HolderConfirmRequest(ApiModel):
    """POST /jobs/{id}/holder-confirm: "I have the raw and the hash matched"."""

    lease_id: str
    sha256: Sha256Hex
    size_bytes: ByteCount
    # From ffprobe on the worker; the first confirming holder fills these in.
    duration_s: Seconds | None = None
    has_audio: bool | None = None


class HolderConfirmResponse(ApiModel):
    confirmed_holder_count: int
    # True when this confirmation was the second one and the VPS dropped its copy.
    raw_deleted_from_vps: bool


class ChunkAccepted(ApiModel):
    """Answer to every accepted chunk of a chunked upload (see docs/api.md)."""

    # How many bytes of the file the receiver has now, i.e. the next offset.
    received_bytes: ByteCount
    # True once the last chunk arrived and the whole-file SHA-256 matched.
    complete: bool


class FailedRequest(ApiModel):
    """POST /jobs/{id}/failed: "I cannot finish this task".

    `scope` says whose fault it is (design §17). With "job" the job fails
    at once and a reviewer decides retry or discard. With "worker" the
    task is re-offered to other workers; the job fails only when a second,
    different worker also reports "worker" for the same task.
    """

    lease_id: str
    # Shown to the reviewer as-is: say what failed, e.g. ffmpeg's last lines.
    message: str = Field(min_length=1, max_length=4000)
    # The failing program's exit code, when there is one.
    exit_code: int | None = None
    # "worker": only this worker could not do it (disk, local ffmpeg crash,
    # network); the task goes to someone else. "job": the input is bad
    # (hash mismatch at the source, undecodable file); the job fails now.
    scope: FailureScope = FailureScope.WORKER


class DeliveredRequest(ApiModel):
    """POST /jobs/{id}/delivered: studio ACK after a direct push from a worker."""

    sha256: Sha256Hex
    size_bytes: ByteCount
    relative_path: str


class OutboxAckRequest(ApiModel):
    """POST /outbox/{id}/ack: studio ACK after pulling a clip from the outbox."""

    lease_id: str
    sha256: Sha256Hex
    relative_path: str


class JobStateResponse(ApiModel):
    """Small answer for endpoints whose only interesting result is the new state."""

    job_id: str
    state: JobState


class Peer(ApiModel):
    """One entry of GET /workers: how to reach another worker over the tailnet."""

    worker_id: str
    name: str
    role: WorkerRole
    tailnet_address: str | None
    file_server_port: int | None
    # Set for studios only: where workers push finished clips.
    receiver_port: int | None
    online: bool
    last_heartbeat: datetime | None


class PeersResponse(ApiModel):
    workers: list[Peer]
