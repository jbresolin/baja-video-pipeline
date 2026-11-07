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
    # A worker reported an unrecoverable error (corrupt file, ffmpeg exit)
    # from received, proxy_ready or claimed; Job.failed_from says which.
    # Waits for a reviewer to choose retry (back there) or discard.
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
    # Master only: copy a finished clip (any version) into its archive
    # folder, from the worker that rendered it or the VPS outbox.
    ARCHIVE_CLIP = "archive_clip"
    # A holder uploads the raw to the VPS's originals/ folder because a
    # reviewer asked for the original on the job page (LIVESTACK-41 item 15).
    UPLOAD_ORIGINAL = "upload_original"


class OriginalState(StrEnum):
    """Where a reviewer's "Baixar original" stands (GET/POST /jobs/{id}/original)."""

    # Nobody asked yet (or the copy expired): the button only asks.
    NOT_REQUESTED = "not_requested"
    # Asked; a holder is online and was offered the upload.
    PREPARING = "preparing"
    # On the VPS: download_url works now.
    READY = "ready"
    # Asked, but no worker that holds the raw is online right now.
    NO_HOLDER_ONLINE = "no_holder_online"


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


class RecordedAtSource(StrEnum):
    """Where Job.recorded_at (when the video was filmed) came from (LIVESTACK-45)."""

    # The raw's own creation_time (ffprobe), plausible: exact.
    METADATA = "metadata"
    # When the filmmaker sent it on WhatsApp: only a hint (filmed before that).
    WHATSAPP = "whatsapp"
    # When the bot received it: the last resort, also only a hint.
    RECEIVED = "received"


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
    # Try again after a worker failure (failed → Job.failed_from; claimed → ready).
    RETRY = "retry"
    # Undo a discard (discarded → Job.discarded_from). Always works within
    # the discard grace period; after it only while a raw copy is left.
    RESTORE = "restore"


class DeliveryRoute(StrEnum):
    # The worker pushed the clip straight to the studio over the tailnet.
    DIRECT = "direct"
    # The worker uploaded to the VPS outbox; the studio pulled it.
    OUTBOX = "outbox"


class FailureScope(StrEnum):
    """Who a POST /jobs/{id}/failed blames (design §17)."""

    # "This worker could not do it" (full disk, local ffmpeg crash, network):
    # the task is offered to someone else; the job fails only after two
    # different workers said so for the same task.
    WORKER = "worker"
    # "The input is bad" (hash mismatch at the source, undecodable file):
    # the job fails at once and waits for a reviewer.
    JOB = "job"


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


class UserRole(StrEnum):
    """What a signed-in user of the review UI may do."""

    # Reviews jobs: the queue, the job page, clips and fleet.
    REVIEWER = "reviewer"
    # Everything a reviewer does, plus the admin pages under /review/admin/.
    ADMIN = "admin"


class ContactStatus(StrEnum):
    """What intake does with a WhatsApp sender (admin Contacts page)."""

    # Approved by an admin: always accepted.
    ALLOWED = "allowed"
    # Never accepted, and the bot stays silent.
    BLOCKED = "blocked"
    # Wrote to the bot, nobody decided yet. Accepted only in "open" intake mode.
    UNKNOWN = "unknown"


class NameStyle(StrEnum):
    """Which name of a team or school the clips show (admin Settings, LIVESTACK-42)."""

    # The full name: "BAJA UFMG", "Universidade Federal de Minas Gerais - UFMG".
    FULL = "full"
    # The short one: the team's short name ("Baja UFMG") or the school's
    # sigla ("UFMG"). When there is none, the full name is used instead.
    SHORT = "short"


class IntakeMode(StrEnum):
    """Who may send videos (admin Settings page)."""

    # Everyone except blocked senders.
    OPEN = "open"
    # Only senders an admin marked "allowed".
    ALLOWLIST = "allowlist"


class CatalogSource(StrEnum):
    """Where a prova or a team row came from."""

    # Typed by an admin (or imported from old files / old jobs).
    MANUAL = "manual"
    # Synced from the Baja results API for the configured event.
    RESULTS_API = "results_api"


class EnrollmentTokenStatus(StrEnum):
    """Where a one-time enrollment token is in its life."""

    UNUSED = "unused"
    USED = "used"
    EXPIRED = "expired"
    REVOKED = "revoked"


class SecretState(StrEnum):
    """Whether a secret from .env is set. The UI never shows the value itself."""

    CONFIGURED = "configured"
    MISSING = "missing"


class AdminArea(StrEnum):
    """One admin page, for GET /admin/audit?area=… (its latest changes)."""

    USERS = "users"
    CONTACTS = "contacts"
    PROVAS = "provas"
    TEAMS = "teams"
    ENROLLMENT = "enrollment"
    WORKERS = "workers"
    SETTINGS = "settings"


class Framing(StrEnum):
    """How a picture that is not 16:9 fills the 1920×1080 clip (LIVESTACK-50).

    Only used when the video, after any rotation, is not 16:9 (a phone
    filmed upright, a 4:3 camera, an ultra-wide one). A 16:9 video renders
    exactly as before, whatever is chosen here.
    """

    # The default ("desfocado"): the whole video centred, the empty sides
    # (or top and bottom) filled with the same video blown up, blurred and
    # darkened, as TV news shows phone footage.
    BLURRED = "blurred"
    # "Barras pretas": the whole video centred on black.
    BLACK_BARS = "black_bars"
    # "Cortar": a 16:9 band of the video fills the frame; the reviewer
    # chooses where the band sits (ProcessTask.crop_position).
    CROP = "crop"


class RenderFeature(StrEnum):
    """A rendering ability a worker version declares and a process task may need.

    Workers list the ones they have (WorkerCapabilities.render_features at
    enrollment, HeartbeatRequest.render_features in every heartbeat); each
    process task lists the ones it needs (ProcessTask.required_render_features),
    and the orchestrator offers a render only to a worker that has all of them
    (docs/api.md §5 "Render features"). A task that needs none goes to any
    worker. On the wire these are plain strings, so a worker may declare a
    feature an older orchestrator has never heard of.
    """

    # Rotation ("Girar") and fitting a non-16:9 picture into 1920×1080
    # (Framing). Installer 0.4.0, LIVESTACK-50.
    FRAMING = "framing"
    # Dropping the sound ("Sem áudio"). Installer 0.4.0, LIVESTACK-50.
    NO_AUDIO = "no_audio"
