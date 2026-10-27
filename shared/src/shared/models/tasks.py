"""Tasks handed to workers by POST /workers/{id}/next and POST /jobs/{id}/claim.

The worker loop is: ask /next what to do → claim it (gets a lease) → do it
→ report the result (holder-confirm, proxy upload, clip upload or ACK),
or POST /jobs/{id}/failed if it cannot succeed.
Each task kind is its own model; the `kind` field tells them apart.
"""

from typing import Annotated, Literal

from pydantic import Field

from shared.models.common import (
    ApiModel,
    ByteCount,
    CarNumber,
    PassNumber,
    Seconds,
    Sha256Hex,
)
from shared.models.enums import DeliveryRoute, TaskKind
from shared.models.job import Lease


class DownloadRawTask(ApiModel):
    """Fetch a raw and become one of its holders.

    Every URL is absolute and ready to GET with Range. The VPS inbox is
    "http://<vps tailscale ip>:8080/inbox/<name>" (Caddy, send your bearer
    token); a peer is "http://<peer tailscale ip>:8710/raw/<job_id>".
    """

    kind: Literal[TaskKind.DOWNLOAD_RAW] = TaskKind.DOWNLOAD_RAW
    job_id: str
    sha256: Sha256Hex
    size_bytes: ByteCount
    # Where to download from first (the VPS inbox while it still has the raw).
    download_url: str
    # Other holders' file servers, to try in order if download_url fails.
    fallback_download_urls: list[str] = Field(default_factory=list)


class MakeProxyTask(ApiModel):
    kind: Literal[TaskKind.MAKE_PROXY] = TaskKind.MAKE_PROXY
    job_id: str
    # The raw the worker already holds (it is a confirmed holder).
    raw_sha256: Sha256Hex
    proxy_upload_url: str
    thumbnail_upload_url: str


class DeliveryPlan(ApiModel):
    """How a process task should deliver its clip; decided by the orchestrator.

    "direct": a studio heartbeated in the last 45 s; push the clip to
    `studio_url` (and fall back to the outbox if that push fails).
    "outbox": no studio is online; upload straight to the outbox.
    The worker never works this out itself from GET /workers (design §17).
    """

    mode: DeliveryRoute
    # "http://<studio tailnet address>:8720/receive/<job_id>" for "direct";
    # None for "outbox".
    studio_url: str | None = None


class ProcessTask(ApiModel):
    kind: Literal[TaskKind.PROCESS] = TaskKind.PROCESS
    job_id: str
    raw_sha256: Sha256Hex
    has_audio: bool
    low_res: bool
    cut_in_s: Seconds
    cut_out_s: Seconds
    prova: str
    car_number: CarNumber
    team_name: str | None
    passagem: PassNumber
    show_pass: bool
    # Where the studio stores the clip, e.g. "enduro/42/3.mp4" (shared.layout).
    clip_relative_path: str
    # Fallback route if the direct push to the studio fails.
    outbox_upload_url: str
    delivery: DeliveryPlan


class DeliverTask(ApiModel):
    """Studio only: pull a clip from the VPS outbox."""

    kind: Literal[TaskKind.DELIVER] = TaskKind.DELIVER
    job_id: str
    # "http://<vps tailscale ip>:8080/outbox/<job_id>.mp4" (Caddy; send your token).
    download_url: str
    sha256: Sha256Hex
    size_bytes: ByteCount
    clip_relative_path: str


# "Any one of the four task models", picked by looking at the `kind` field.
Task = Annotated[
    DownloadRawTask | MakeProxyTask | ProcessTask | DeliverTask,
    Field(discriminator="kind"),
]


class NextTaskRequest(ApiModel):
    """Body of POST /workers/{id}/next: which kinds the worker can take now.

    Design §5: at most one job per stage, so a worker that is already
    processing omits "process" from this list.
    """

    accept_kinds: list[TaskKind] = Field(min_length=1)


class NextTaskResponse(ApiModel):
    """An offer, not a reservation: the worker must claim it before working.

    If two workers receive the same offer, only the first claim wins; the
    other gets 409 already_claimed and simply asks /next again.
    """

    # None means "nothing for you right now".
    task: Task | None = None
    retry_after_s: int = Field(default=5, ge=0)


class ClaimRequest(ApiModel):
    task_kind: TaskKind


class ClaimResponse(ApiModel):
    lease: Lease
    # The task again, with up-to-date details (e.g. cuts edited meanwhile).
    task: Task
