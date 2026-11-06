"""Enrollment tokens list and operator settings (admin page, LIVESTACK-37)."""

from datetime import datetime

from pydantic import Field

from shared.models.api_admin import ServerDisk
from shared.models.api_catalog import ResultsSyncReport
from shared.models.common import ApiModel
from shared.models.enums import (
    EnrollmentTokenStatus,
    IntakeMode,
    NameStyle,
    SecretState,
    WorkerRole,
)

# --- Enrollment tokens ---


class EnrollmentTokenInfo(ApiModel):
    """One token on the admin Enrollment page. The token itself is never shown again."""

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


class EnrollmentTokensResponse(ApiModel):
    # Newest first.
    tokens: list[EnrollmentTokenInfo]


# --- Workers ---


class UpdateWorkerRequest(ApiModel):
    """PATCH /admin/workers/{worker_id}: the worker's display name (LIVESTACK-42).

    The display name is what every page shows instead of the host name,
    which may mean nothing ("DESKTOP-8F3K2LQ"). Blank names are refused.
    """

    name: str = Field(min_length=1, max_length=100, pattern=r"\S")


# --- Settings ---

# An event id of the results API: 4 letters or digits ("26BR"), case kept.
# Empty = no event configured (no sync).
EVENT_ID_PATTERN = r"^([A-Za-z0-9]{4})?$"


class AdminSettings(ApiModel):
    """Settings the operator edits in the UI. The .env values are only first-start defaults."""

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


class UpdateSettingsRequest(ApiModel):
    """PATCH /admin/settings. Fields left out keep their value."""

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


class EvolutionStatus(ApiModel):
    """What Evolution says about the Baja Bot instance, asked live."""

    instance: str
    # "open" (connected), "connecting", "close"; None if Evolution did not answer.
    state: str | None = None
    webhook_url: str | None = None
    webhook_enabled: bool | None = None
    # Why state or webhook is unknown, e.g. "connection refused".
    error: str | None = None


class SettingsStatus(ApiModel):
    """The read-only block of the Settings page."""

    evolution: EvolutionStatus
    # Each secret from .env by name ("BVP_ADMIN_TOKEN", ...): configured or missing.
    secrets: dict[str, SecretState]
    server_disk: ServerDisk
    # BVP_INBOX_CAP_GB, the hard cap (also the inbox filesystem's size): not editable.
    inbox_cap_gb: float
    # The last provas/teams sync with the results API; None before the first.
    results_sync: ResultsSyncReport | None = None


class SettingsResponse(ApiModel):
    settings: AdminSettings
    status: SettingsStatus
