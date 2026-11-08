"""Enrollment tokens list and operator settings (admin page, LIVESTACK-37)."""

from datetime import datetime

from pydantic import Field

from shared.duplicate_video_message import (
    DEFAULT_DUPLICATE_VIDEO_MESSAGE,
    MAX_DUPLICATE_VIDEO_MESSAGE_LENGTH,
)
from shared.models.api_admin import ServerDisk
from shared.models.api_catalog import ResultsSyncReport
from shared.models.common import ReadByClients, ReceivedByServer
from shared.models.enums import (
    EnrollmentTokenStatus,
    IntakeMode,
    NameStyle,
    SecretState,
    WorkerRole,
)
from shared.vertical_video_message import (
    DEFAULT_VERTICAL_VIDEO_MESSAGE,
    MAX_VERTICAL_VIDEO_MESSAGE_LENGTH,
)
from shared.welcome_message import DEFAULT_WELCOME_MESSAGE, MAX_WELCOME_MESSAGE_LENGTH

# --- Enrollment tokens ---


class EnrollmentTokenInfo(ReadByClients):
    """One token on the admin Enrollment page. The token itself is never shown again.

    Read by clients: unknown fields are ignored.
    """

    # Short public id (the first 16 hex characters of the token's SHA-256),
    # used to revoke it. Reveals nothing about the token.
    token_id: str
    volunteer_name: str
    role: WorkerRole
    status: EnrollmentTokenStatus
    created_at: datetime
    expires_at: datetime
    used_at: datetime | None = None
    used_by_worker_id: str | None = None
    used_by_worker_name: str | None = None
    revoked_at: datetime | None = None


class EnrollmentTokensResponse(ReadByClients):
    """Read by clients: unknown fields are ignored."""

    # Newest first.
    tokens: list[EnrollmentTokenInfo]


# --- Workers ---


class UpdateWorkerRequest(ReceivedByServer):
    """PATCH /admin/workers/{worker_id}: the worker's display name (LIVESTACK-42).

    The display name is what every page shows instead of the host name,
    which may mean nothing ("DESKTOP-8F3K2LQ"). Blank names are refused.

    Received by the orchestrator: unknown fields are refused.
    """

    name: str = Field(min_length=1, max_length=100, pattern=r"\S")


# --- Settings ---

# An event id of the results API: 4 letters or digits ("26BR"), case kept.
# Empty = no event configured (no sync).
EVENT_ID_PATTERN = r"^([A-Za-z0-9]{4})?$"


class AdminSettings(ReadByClients):
    """Settings the operator edits in the UI. The .env values are only first-start defaults.

    Read by clients: unknown fields are ignored.
    """

    # Who may send videos: everyone but blocked senders, or allowed ones only.
    intake_mode: IntakeMode
    # Soft inbox cap: above it the bot asks senders to hold (≤ BVP_INBOX_CAP_GB).
    inbox_hold_gb: float = Field(gt=0)
    # Evict the oldest finished proxies when free disk drops below this.
    proxy_evict_min_free_gb: float = Field(ge=0)
    # Link shown next to a new enrollment token, e.g. the latest GitHub
    # Release asset of the worker installer. Empty = no link.
    installer_download_url: str = Field(max_length=500)
    # The current event in the Baja results API; provas and teams are synced
    # from it (on change, and every 6 h). Case matters: "26br" is not "26BR".
    event_id: str = Field(default="", pattern=EVENT_ID_PATTERN)
    results_api_base_url: str = Field(default="https://api.bajasaebrasil.net", max_length=200)
    # A discarded job's raws stay on the holders and in the VPS inbox this
    # long, so "Restaurar" just works (LIVESTACK-38). The end is fixed on
    # the job at discard time: a change here moves no existing deadline.
    discard_grace_hours: float = Field(default=24, ge=0, le=720)
    # An outbox clip the studio already received is kept at most this long
    # waiting for the master to archive it (master offline), LIVESTACK-38.
    outbox_keep_unarchived_hours: float = Field(default=72, ge=0, le=720)
    # Which names the clips show (LIVESTACK-42): the team's full or short
    # name, and the school's full name or sigla. Each falls back to the
    # other when the chosen one is missing. They drive the overlay text and
    # every place the UI shows a clip's team and school.
    clip_team_name_style: NameStyle = NameStyle.SHORT
    clip_school_name_style: NameStyle = NameStyle.SHORT
    # Originals a reviewer downloaded ("Baixar original", LIVESTACK-41 item
    # 15) stay on the VPS this long after the last request...
    originals_keep_hours: float = Field(default=24, ge=0, le=720)
    # ...and the originals/ folder never grows past this (oldest go first).
    originals_cap_gb: float = Field(default=10, ge=0)
    # Sent by the bot to a contact the admin adds, or on "Enviar boas-vindas"
    # (LIVESTACK-44); {nome} and {prova} are filled in (shared.welcome_message).
    # Empty = no welcome message: the Contacts page disables both controls.
    welcome_message: str = Field(
        default=DEFAULT_WELCOME_MESSAGE, max_length=MAX_WELCOME_MESSAGE_LENGTH
    )
    # Quoted by the bot on a video whose probe shows it was filmed upright
    # (LIVESTACK-50), once per video. Empty = no such reply.
    vertical_video_message: str = Field(
        default=DEFAULT_VERTICAL_VIDEO_MESSAGE, max_length=MAX_VERTICAL_VIDEO_MESSAGE_LENGTH
    )
    # Filmmakers' messages other than videos (texts, captions, "(áudio)"),
    # shown on the job page (LIVESTACK-49), are deleted after this many days.
    sender_messages_keep_days: int = Field(default=14, ge=1, le=365)
    # Quoted by the bot on a video it already had (LIVESTACK-51), once per
    # copy; {hora} = when the first copy arrived. Empty = no such reply.
    duplicate_video_message: str = Field(
        default=DEFAULT_DUPLICATE_VIDEO_MESSAGE, max_length=MAX_DUPLICATE_VIDEO_MESSAGE_LENGTH
    )
    # "Gerar prévia dos clipes" (LIVESTACK-52): after a render, the worker
    # makes a 480p preview of the clip for "Ver clipe". Off for an event
    # where the VPS disk is tight; clips rendered while off have none.
    clip_previews_enabled: bool = True
    # "Baixar clipe" (LIVESTACK-52): the VPS copy goes this many minutes
    # after its first complete download (time to retry a broken one)...
    clip_download_keep_minutes: float = Field(default=30, ge=0, le=1440)
    # ...or this many hours after the request when nobody downloads it.
    clip_download_unused_hours: float = Field(default=24, ge=0, le=720)


class UpdateSettingsRequest(ReceivedByServer):
    """PATCH /admin/settings. Fields left out keep their value.

    Received by the orchestrator: unknown fields are refused.
    """

    intake_mode: IntakeMode | None = None
    inbox_hold_gb: float | None = Field(default=None, gt=0)
    proxy_evict_min_free_gb: float | None = Field(default=None, ge=0)
    installer_download_url: str | None = Field(default=None, max_length=500)
    event_id: str | None = Field(default=None, pattern=EVENT_ID_PATTERN)
    results_api_base_url: str | None = Field(default=None, max_length=200)
    discard_grace_hours: float | None = Field(default=None, ge=0, le=720)
    outbox_keep_unarchived_hours: float | None = Field(default=None, ge=0, le=720)
    clip_team_name_style: NameStyle | None = None
    clip_school_name_style: NameStyle | None = None
    originals_keep_hours: float | None = Field(default=None, ge=0, le=720)
    originals_cap_gb: float | None = Field(default=None, ge=0)
    # "" empties it (no welcome message). Line ends are stored as \n and the
    # ends trimmed, then the stored text may be at most 2000 characters; a
    # browser sends each line end as two, hence the larger limit here.
    welcome_message: str | None = Field(default=None, max_length=2 * MAX_WELCOME_MESSAGE_LENGTH)
    # "" turns the reply to vertical videos off. Stored like the welcome message.
    vertical_video_message: str | None = Field(
        default=None, max_length=2 * MAX_VERTICAL_VIDEO_MESSAGE_LENGTH
    )
    sender_messages_keep_days: int | None = Field(default=None, ge=1, le=365)
    # "" turns the reply to duplicate videos off. Stored like the welcome message.
    duplicate_video_message: str | None = Field(
        default=None, max_length=2 * MAX_DUPLICATE_VIDEO_MESSAGE_LENGTH
    )
    clip_previews_enabled: bool | None = None
    clip_download_keep_minutes: float | None = Field(default=None, ge=0, le=1440)
    clip_download_unused_hours: float | None = Field(default=None, ge=0, le=720)


class EvolutionStatus(ReadByClients):
    """What Evolution says about the Baja Bot instance, asked live.

    Read by clients: unknown fields are ignored.
    """

    instance: str
    # "open" (connected), "connecting", "close"; None if Evolution did not answer.
    state: str | None = None
    webhook_url: str | None = None
    webhook_enabled: bool | None = None
    # Why state or webhook is unknown, e.g. "connection refused".
    error: str | None = None


class SettingsStatus(ReadByClients):
    """The read-only block of the Settings page.

    Read by clients: unknown fields are ignored.
    """

    evolution: EvolutionStatus
    # Each secret from .env by name ("BVP_ADMIN_TOKEN", ...): configured or missing.
    secrets: dict[str, SecretState]
    server_disk: ServerDisk
    # BVP_INBOX_CAP_GB, the hard cap (also the inbox filesystem's size): not editable.
    inbox_cap_gb: float
    # The last provas/teams sync with the results API; None before the first.
    results_sync: ResultsSyncReport | None = None


class SettingsResponse(ReadByClients):
    """Read by clients: unknown fields are ignored."""

    settings: AdminSettings
    status: SettingsStatus
