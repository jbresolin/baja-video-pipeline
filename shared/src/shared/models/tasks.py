"""Tasks handed to workers by POST /workers/{id}/next and POST /jobs/{id}/claim.

The worker loop is: ask /next what to do → claim it (gets a lease) → do it
→ report the result (holder-confirm, proxy upload, clip upload or ACK),
or POST /jobs/{id}/failed if it cannot succeed.
Each task kind is its own model; the `kind` field tells them apart.
"""

from typing import Annotated, Literal

from pydantic import Field

from shared.models.common import (
    ByteCount,
    CarNumber,
    PassNumber,
    ReadByClients,
    ReceivedByServer,
    Seconds,
    Sha256Hex,
)
from shared.models.enums import DeliveryRoute, TaskKind
from shared.models.job import Lease


class DownloadRawTask(ReadByClients):
    """Fetch a raw and become one of its holders.

    Every URL is absolute and ready to GET with Range. The VPS inbox is
    "http://<vps tailscale ip>:8080/inbox/<name>" (Caddy, send your bearer
    token); a peer is "http://<peer tailscale ip>:8710/raw/<job_id>".

    Read by clients: unknown fields are ignored.
    """

    kind: Literal[TaskKind.DOWNLOAD_RAW] = TaskKind.DOWNLOAD_RAW
    job_id: str
    sha256: Sha256Hex
    size_bytes: ByteCount
    # Where to download from first (the VPS inbox while it still has the raw).
    download_url: str
    # Other holders' file servers, to try in order if download_url fails.
    fallback_download_urls: list[str] = Field(default_factory=list)
    # The name to store the raw under on the worker's disk (LIVESTACK-41
    # item 12), e.g. "joao-guilherme-bresolin-20261104-232909-2f2510a3.mp4".
    # None for older jobs (and from older orchestrators): "<job_id><ext>".
    raw_file_name: str | None = None


class MakeProxyTask(ReadByClients):
    """Read by clients: unknown fields are ignored."""

    kind: Literal[TaskKind.MAKE_PROXY] = TaskKind.MAKE_PROXY
    job_id: str
    # The raw the worker already holds (it is a confirmed holder).
    raw_sha256: Sha256Hex
    proxy_upload_url: str
    thumbnail_upload_url: str
    # PUT the raw's ffprobe report here (ProbeReport JSON) before the proxy.
    # None from an orchestrator older than contract 1.2.0: skip it then.
    media_info_upload_url: str | None = None


class DeliveryPlan(ReadByClients):
    """How a process task should deliver its clip; decided by the orchestrator.

    "direct": a studio heartbeated in the last 45 s; push the clip to
    `studio_url` (and fall back to the outbox if that push fails).
    "outbox": no studio is online; upload straight to the outbox.
    The worker never works this out itself from GET /workers (design §17).

    Read by clients: unknown fields are ignored.
    """

    mode: DeliveryRoute
    # "http://<studio tailnet address>:8720/receive/<job_id>" for "direct";
    # None for "outbox".
    studio_url: str | None = None


class ProcessTask(ReadByClients):
    """Read by clients: unknown fields are ignored."""

    kind: Literal[TaskKind.PROCESS] = TaskKind.PROCESS
    job_id: str
    raw_sha256: Sha256Hex
    has_audio: bool
    low_res: bool
    cut_in_s: Seconds
    cut_out_s: Seconds
    prova: str
    car_number: CarNumber
    # The name the overlay shows for the team: its full or short name, as
    # the admin chose on the Settings page (default short; LIVESTACK-42),
    # the other one when that is missing. None when the car is unknown.
    team_name: str | None
    # The school for the overlay's role pill. Despite the name, it is the
    # school's sigla ("UFMG", the default) or its full name, as chosen on
    # the Settings page; kept under this name so workers need no change.
    # None when unknown; the overlay then leaves it out.
    school_short: str | None = None
    passagem: PassNumber
    show_pass: bool
    # Where the studio stores the clip, e.g. "enduro/42/3.mp4" (shared.layout).
    clip_relative_path: str
    # Fallback route if the direct push to the studio fails.
    outbox_upload_url: str
    delivery: DeliveryPlan


class DeliverTask(ReadByClients):
    """Studio only: pull a clip from the VPS outbox.

    Read by clients: unknown fields are ignored.
    """

    kind: Literal[TaskKind.DELIVER] = TaskKind.DELIVER
    job_id: str
    # "http://<vps tailscale ip>:8080/outbox/<job_id>.mp4" (Caddy; send your token).
    download_url: str
    sha256: Sha256Hex
    size_bytes: ByteCount
    clip_relative_path: str


class ArchiveClipTask(ReadByClients):
    """Master only: keep a copy of one finished clip version in the archive folder.

    Lowest priority, after live work and raw backfill. If the master has
    the clip itself (it rendered it), it moves its own copy; otherwise it
    GETs the first URL that works: the rendering worker's file server
    ("http://<peer>:8710/clip/<job_id>?sha256=…", no token) or the VPS outbox
    (Caddy, send your token). Then POST /jobs/{id}/clip-archived.

    Read by clients: unknown fields are ignored.
    """

    kind: Literal[TaskKind.ARCHIVE_CLIP] = TaskKind.ARCHIVE_CLIP
    job_id: str
    version: int = Field(ge=1)
    sha256: Sha256Hex
    size_bytes: ByteCount
    # Where to store it under the archive folder (shared.layout.archive_relative_path).
    archive_relative_path: str
    # Sources in order; may be empty when the master itself holds the clip.
    download_urls: list[str] = Field(default_factory=list)


class UploadOriginalTask(ReadByClients):
    """A holder sends the raw to the VPS for a reviewer (LIVESTACK-41 item 15).

    The 480p proxy is too small to read a car number or judge a close
    finish, so the job page can ask for the original. The VPS no longer has
    it (it is transit only), so a holder uploads it, chunked and resumable
    like a clip, to `upload_url`; the VPS keeps it in originals/ for a day.
    The upload ends the lease. Never counts as a holder.

    Read by clients: unknown fields are ignored.
    """

    kind: Literal[TaskKind.UPLOAD_ORIGINAL] = TaskKind.UPLOAD_ORIGINAL
    job_id: str
    raw_sha256: Sha256Hex
    size_bytes: ByteCount
    # POST chunks here (Lease-Id header), like POST /jobs/{id}/clip.
    upload_url: str


# "Any one of the task models", picked by looking at the `kind` field.
Task = Annotated[
    DownloadRawTask
    | MakeProxyTask
    | ProcessTask
    | DeliverTask
    | ArchiveClipTask
    | UploadOriginalTask,
    Field(discriminator="kind"),
]


class NextTaskRequest(ReceivedByServer):
    """Body of POST /workers/{id}/next: which kinds the worker can take now.

    Design §5: at most one job per stage, so a worker that is already
    processing omits "process" from this list.

    Received by the orchestrator: unknown fields are refused.
    """

    accept_kinds: list[TaskKind] = Field(min_length=1)


class NextTaskResponse(ReadByClients):
    """An offer, not a reservation: the worker must claim it before working.

    If two workers receive the same offer, only the first claim wins; the
    other gets 409 already_claimed and simply asks /next again.

    Read by clients: unknown fields are ignored.
    """

    # None means "nothing for you right now".
    task: Task | None = None
    retry_after_s: int = Field(default=5, ge=0)


class ClaimRequest(ReceivedByServer):
    """Received by the orchestrator: unknown fields are refused."""

    task_kind: TaskKind


class ClaimResponse(ReadByClients):
    """Read by clients: unknown fields are ignored."""

    lease: Lease
    # The task again, with up-to-date details (e.g. cuts edited meanwhile).
    task: Task
