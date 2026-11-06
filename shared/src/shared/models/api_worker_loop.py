"""Request/response bodies for the worker loop endpoints (bearer auth)."""

from datetime import datetime

from pydantic import Field

from shared.models.common import ByteCount, ReadByClients, ReceivedByServer, Seconds, Sha256Hex
from shared.models.enums import (
    FailureScope,
    JobState,
    LeaseHeartbeatStatus,
    MeasurementKind,
    WorkerRole,
)
from shared.models.job import Lease


class Measurement(ReceivedByServer):
    """One timed transfer or render, e.g. "downloaded 500 MB in 42 s".

    Received by the orchestrator: unknown fields are refused.
    """

    kind: MeasurementKind
    byte_count: ByteCount
    seconds: float = Field(gt=0)


class HeldClip(ReceivedByServer):
    """A finished clip a worker keeps in its clips/ folder (served on :8710).

    Received by the orchestrator: unknown fields are refused.
    """

    job_id: str
    sha256: Sha256Hex


class HeartbeatRequest(ReceivedByServer):
    """POST /workers/{id}/heartbeat, every 15 s.

    Received by the orchestrator: unknown fields are refused.
    """

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
    # Clips this worker rendered and keeps (design §11 as amended: clips are
    # the archive). The master copies them; the answer says which it has.
    held_clips: list[HeldClip] = Field(default_factory=list)
    # True while the volunteer has paused this worker ("Pausar" in the tray
    # icon or on the status page). The worker finishes the task it is on and
    # asks for nothing new; the orchestrator offers it nothing and treats it
    # like an offline worker for second-holder and studio choice, but it
    # still serves its held raws to peers.
    paused: bool = False
    # The role this worker is running as (LIVESTACK-42). Sending it also
    # asks for `role` in the answer; workers that do not send it (older
    # versions) never get that field, which their parser would refuse.
    role: WorkerRole | None = None
    # True when the volunteer chose "Usar a rede atual" at "Concluir conexão"
    # (LIVESTACK-41 item 4): this PC stays on its own tailnet, into which the
    # VPS is shared. It reaches the orchestrator, but peers and the studio
    # cannot reach it, so it is never a second holder or a peer source, and
    # its clips go through the outbox.
    shared_tailnet: bool = False


class HeartbeatResponse(ReadByClients):
    """Read by clients: unknown fields are ignored."""

    # For every lease id the worker sent: renewed, or lost (stop that task).
    lease_status: dict[str, LeaseHeartbeatStatus] = Field(default_factory=dict)
    heartbeat_interval_s: int
    # False when the worker is near its storage cap: it will not be offered
    # new download_raw tasks but keeps processing what it holds (design §9).
    accepting_new_raws: bool
    # Jobs from held_job_ids that were discarded: the worker may delete
    # its copy of their raw (design §4). Never lists a job still in use.
    deletable_job_ids: list[str] = Field(default_factory=list)
    # SHA-256 of the held_clips the master has archived. Only these may be
    # evicted, and only when near the storage cap, oldest first.
    archived_clip_sha256s: list[Sha256Hex] = Field(default_factory=list)
    # The role the orchestrator has for this worker, sent only when the
    # request carried `role` (left out of the JSON otherwise). An admin may
    # switch master and worker on Admin → Workers ("Tornar master"); a
    # worker whose running role differs saves this one and restarts.
    role: WorkerRole | None = None


class LeaseRenewRequest(ReceivedByServer):
    """Received by the orchestrator: unknown fields are refused."""

    lease_id: str


class LeaseRenewResponse(ReadByClients):
    """Read by clients: unknown fields are ignored."""

    lease: Lease


class HolderConfirmRequest(ReceivedByServer):
    """POST /jobs/{id}/holder-confirm: "I have the raw and the hash matched".

    Received by the orchestrator: unknown fields are refused.
    """

    lease_id: str
    sha256: Sha256Hex
    size_bytes: ByteCount
    # From ffprobe on the worker; the first confirming holder fills these in.
    duration_s: Seconds | None = None
    has_audio: bool | None = None


class ClipArchivedRequest(ReceivedByServer):
    """POST /jobs/{id}/clip-archived: the master stored that exact clip, hash checked.

    Received by the orchestrator: unknown fields are refused.
    """

    lease_id: str
    sha256: Sha256Hex


class HolderConfirmResponse(ReadByClients):
    """Read by clients: unknown fields are ignored."""

    confirmed_holder_count: int
    # True when this confirmation was the second one and the VPS dropped its copy.
    raw_deleted_from_vps: bool


class ChunkAccepted(ReadByClients):
    """Answer to every accepted chunk of a chunked upload (see docs/api.md).

    Read by clients: unknown fields are ignored.
    """

    # How many bytes of the file the receiver has now, i.e. the next offset.
    received_bytes: ByteCount
    # True once the last chunk arrived and the whole-file SHA-256 matched.
    complete: bool


class FailedRequest(ReceivedByServer):
    """POST /jobs/{id}/failed: "I cannot finish this task".

    `scope` says whose fault it is (design §17). With "job" the job fails
    at once and a reviewer decides retry or discard. With "worker" the
    task is re-offered to other workers; the job fails only when a second,
    different worker also reports "worker" for the same task.

    Received by the orchestrator: unknown fields are refused.
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


class DeliveredRequest(ReceivedByServer):
    """POST /jobs/{id}/delivered: studio ACK after a direct push from a worker.

    Received by the orchestrator: unknown fields are refused.
    """

    sha256: Sha256Hex
    size_bytes: ByteCount
    relative_path: str


class OutboxAckRequest(ReceivedByServer):
    """POST /outbox/{id}/ack: studio ACK after pulling a clip from the outbox.

    Received by the orchestrator: unknown fields are refused.
    """

    lease_id: str
    sha256: Sha256Hex
    relative_path: str


class JobStateResponse(ReadByClients):
    """Small answer for endpoints whose only interesting result is the new state.

    Read by clients: unknown fields are ignored.
    """

    job_id: str
    state: JobState


class Peer(ReadByClients):
    """One entry of GET /workers: how to reach another worker over the tailnet.

    Read by clients: unknown fields are ignored.
    """

    worker_id: str
    name: str
    role: WorkerRole
    tailnet_address: str | None
    file_server_port: int | None
    # Set for studios only: where workers push finished clips.
    receiver_port: int | None
    online: bool
    last_heartbeat: datetime | None


class PeersResponse(ReadByClients):
    """Read by clients: unknown fields are ignored."""

    workers: list[Peer]
