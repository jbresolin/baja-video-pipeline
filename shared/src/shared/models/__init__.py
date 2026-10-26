"""All pydantic models, importable from one place: `from shared.models import Job`.

The models are split over several files only to keep each file short:
    enums.py            fixed value lists (JobState, WorkerRole, ...)
    common.py           field types, ErrorResponse
    job.py              Job, Lease, Holder, ClipInfo
    worker.py           Worker, WorkerCapabilities, EnrollmentToken
    tasks.py            the four task kinds handed out by /next
    api_worker_loop.py  heartbeat, holder-confirm, uploads, ACKs, peers
    api_review.py       review queue and review update
    api_admin.py        intake, enrollment, fleet, manifest
    evolution.py        the Evolution webhook payload
"""

from shared.models.api_admin import (
    CreateEnrollmentTokenRequest,
    EnrollmentTokenCreated,
    EnrollRequest,
    EnrollResponse,
    FleetResponse,
    FleetWorker,
    IntakeResponse,
    ManifestEntry,
    ManifestResponse,
    ServerDisk,
)
from shared.models.api_review import (
    RegenerateProxyResponse,
    ReviewQueueFilter,
    ReviewQueueResponse,
    ReviewUpdate,
    Team,
)
from shared.models.api_worker_loop import (
    ChunkAccepted,
    DeliveredRequest,
    HeartbeatRequest,
    HeartbeatResponse,
    HolderConfirmRequest,
    HolderConfirmResponse,
    JobStateResponse,
    LeaseRenewRequest,
    LeaseRenewResponse,
    Measurement,
    OutboxAckRequest,
    Peer,
    PeersResponse,
)
from shared.models.common import ErrorBody, ErrorCode, ErrorResponse, utc_now
from shared.models.enums import (
    DeliveryRoute,
    HolderStatus,
    JobState,
    LeaseHeartbeatStatus,
    MeasurementKind,
    RawSourceKind,
    ReceivedAs,
    ReviewAction,
    TaskKind,
    WorkerRole,
)
from shared.models.evolution import EvolutionWebhook, IncomingMedia, find_incoming_video
from shared.models.job import ClipInfo, Holder, IntakeSource, Job, JobSummary, Lease
from shared.models.tasks import (
    ClaimRequest,
    ClaimResponse,
    DeliverTask,
    DownloadRawTask,
    MakeProxyTask,
    NextTaskRequest,
    NextTaskResponse,
    ProcessTask,
    RawSource,
    Task,
)
from shared.models.worker import EnrollmentToken, Worker, WorkerCapabilities, WorkerStats

__all__ = [
    "ChunkAccepted",
    "ClaimRequest",
    "ClaimResponse",
    "ClipInfo",
    "CreateEnrollmentTokenRequest",
    "DeliverTask",
    "DeliveredRequest",
    "DeliveryRoute",
    "DownloadRawTask",
    "EnrollRequest",
    "EnrollResponse",
    "EnrollmentToken",
    "EnrollmentTokenCreated",
    "ErrorBody",
    "ErrorCode",
    "ErrorResponse",
    "EvolutionWebhook",
    "FleetResponse",
    "FleetWorker",
    "HeartbeatRequest",
    "HeartbeatResponse",
    "Holder",
    "HolderConfirmRequest",
    "HolderConfirmResponse",
    "HolderStatus",
    "IncomingMedia",
    "IntakeResponse",
    "IntakeSource",
    "Job",
    "JobState",
    "JobStateResponse",
    "JobSummary",
    "Lease",
    "LeaseHeartbeatStatus",
    "LeaseRenewRequest",
    "LeaseRenewResponse",
    "MakeProxyTask",
    "ManifestEntry",
    "ManifestResponse",
    "Measurement",
    "MeasurementKind",
    "NextTaskRequest",
    "NextTaskResponse",
    "OutboxAckRequest",
    "Peer",
    "PeersResponse",
    "ProcessTask",
    "RawSource",
    "RawSourceKind",
    "ReceivedAs",
    "RegenerateProxyResponse",
    "ReviewAction",
    "ReviewQueueFilter",
    "ReviewQueueResponse",
    "ReviewUpdate",
    "ServerDisk",
    "Task",
    "TaskKind",
    "Team",
    "Worker",
    "WorkerCapabilities",
    "WorkerRole",
    "WorkerStats",
    "find_incoming_video",
    "utc_now",
]
