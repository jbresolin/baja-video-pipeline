"""Every fixed list of values used in the API.

They are StrEnums, so each member *is* a string: JobState.READY == "ready".
That keeps JSON and the SQLite database human-readable.
"""

from enum import StrEnum


class JobState(StrEnum):
    """Where a job is in its life (design §4). Order of the happy path:

    received → proxy_ready → awaiting_review → ready → claimed → uploaded
    → delivered → done
    """

    # The raw is in the VPS inbox and its SHA-256 is known. Holders download it.
    RECEIVED = "received"
    # A holder uploaded the 480p proxy. The orchestrator moves the job on to
    # awaiting_review straight away; the state exists so the step is visible
    # in history and so a proxy upload is a distinct, testable event.
    PROXY_READY = "proxy_ready"
    # In the review queue: a human must set the cut and the fields.
    AWAITING_REVIEW = "awaiting_review"
    # Reviewed; waiting for a holder to claim the processing.
    READY = "ready"
    # A holder is rendering the final clip under a lease.
    CLAIMED = "claimed"
    # The finished clip left the worker: it is in the VPS outbox (fallback
    # route) or was pushed straight to the studio (direct route).
    UPLOADED = "uploaded"
    # The studio box acknowledged a verified copy of the clip.
    DELIVERED = "delivered"
    # Cleanup after delivery finished (outbox copy deleted). Nothing left to do.
    DONE = "done"
    # A reviewer dropped a junk or accidental video. Final: nothing more
    # happens, and holders may delete their copy of the raw.
    DISCARDED = "discarded"
    # A worker reported an unrecoverable error while processing (corrupt
    # file, ffmpeg exit). Waits for a reviewer to choose retry or discard.
    FAILED = "failed"


class WorkerRole(StrEnum):
    # João's desktop: always one of the two holders of every raw.
    MASTER = "master"
    # Volunteer PCs: hold raws, make proxies, render clips.
    WORKER = "worker"
    # The streaming PC: never holds raws, receives finished clips.
    STUDIO = "studio"


class TaskKind(StrEnum):
    """The kinds of work /workers/{id}/next can hand out."""

    # Fetch a raw (from the VPS or a peer), verify its hash, become a holder.
    DOWNLOAD_RAW = "download_raw"
    # Render the 480p proxy and thumbnail from a held raw and upload them.
    MAKE_PROXY = "make_proxy"
    # Render the final clip (trim + overlay + loudnorm) and deliver it.
    PROCESS = "process"
    # Studio only: pull a clip from the VPS outbox, verify it, store it, ACK.
    DELIVER = "deliver"


class HolderStatus(StrEnum):
    # The orchestrator picked this worker as a holder; the download is running.
    ASSIGNED = "assigned"
    # The worker has the raw on disk and its SHA-256 matched.
    CONFIRMED = "confirmed"


class ReceivedAs(StrEnum):
    """How the raw reached the Baja Bot."""

    # Sent as a WhatsApp *document*: original quality.
    DOCUMENT = "document"
    # Sent as a WhatsApp *video*: recompressed, so the job is flagged low_res.
    VIDEO = "video"
    # Uploaded through the dev-only test endpoint.
    TEST_UPLOAD = "test_upload"


class ReviewAction(StrEnum):
    """What a PATCH /jobs/{id}/review should do besides storing the fields."""

    # Store the fields and cuts; do not change state (work in progress).
    SAVE = "save"
    # Store and move awaiting_review → ready (all fields and cuts required).
    READY = "ready"
    # Store and send an already processed job back to ready (cut changed).
    REPROCESS = "reprocess"
    # Drop the job (awaiting_review, ready or failed → discarded).
    DISCARD = "discard"
    # Try again after a worker failure (failed → ready).
    RETRY = "retry"


class DeliveryRoute(StrEnum):
    # The worker pushed the clip straight to the studio over the tailnet.
    DIRECT = "direct"
    # The worker uploaded to the VPS outbox; the studio pulled it.
    OUTBOX = "outbox"


class MeasurementKind(StrEnum):
    """What a worker timed, to feed the rolling stats (design §5)."""

    DOWNLOAD = "download"
    PROCESS = "process"
    UPLOAD = "upload"


class LeaseHeartbeatStatus(StrEnum):
    # The lease was extended by this heartbeat.
    RENEWED = "renewed"
    # The lease no longer exists (expired, or a reviewer changed the job).
    # The worker must stop that task and throw away its partial output.
    LOST = "lost"
