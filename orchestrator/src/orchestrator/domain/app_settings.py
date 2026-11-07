"""Operator settings edited on the admin Settings page.

Stored in the `app_settings` table. The .env values (BVP_INTAKE_MODE,
BVP_INBOX_HOLD_GB, BVP_PROXY_EVICT_MIN_FREE_GB, BVP_INSTALLER_DOWNLOAD_URL,
BVP_EVENT_ID, BVP_RESULTS_API_URL, BVP_DISCARD_GRACE_HOURS,
BVP_OUTBOX_KEEP_UNARCHIVED_HOURS)
are only used for a key that is not in the table yet, i.e. on the first
start; after that the table wins and editing .env changes nothing.

The current values are kept in memory (AdminSettingsStore) because intake
and the proxy sweeper read them often; every change goes to the table and
the memory copy together.
"""

import threading
from datetime import datetime, timedelta

from orchestrator.clock import Clock
from orchestrator.config import BYTES_PER_GB, Settings
from orchestrator.db.database import Database
from orchestrator.domain.audit import AdminActor, record_admin_change
from orchestrator.errors import validation_error
from orchestrator.repositories import app_settings as settings_repo
from shared.models import AdminSettings, UpdateSettingsRequest
from shared.vertical_video_message import (
    MAX_VERTICAL_VIDEO_MESSAGE_LENGTH,
    normalize_vertical_video_message,
)
from shared.welcome_message import MAX_WELCOME_MESSAGE_LENGTH, normalize_welcome_message


def defaults_from_environment(settings: Settings) -> AdminSettings:
    return AdminSettings(
        intake_mode=settings.intake_mode,
        inbox_hold_gb=settings.inbox_hold_gb,
        proxy_evict_min_free_gb=settings.proxy_evict_min_free_gb,
        installer_download_url=settings.installer_download_url,
        event_id=settings.event_id,
        results_api_base_url=settings.results_api_url,
        discard_grace_hours=settings.discard_grace_hours,
        outbox_keep_unarchived_hours=settings.outbox_keep_unarchived_hours,
    )


class AdminSettingsStore:
    def __init__(self, database: Database, environment: Settings, clock: Clock) -> None:
        self._database = database
        self._inbox_cap_gb = environment.inbox_cap_gb
        self._lock = threading.Lock()
        self._current = self._load_seeding_missing_keys(environment, clock.now())

    def _load_seeding_missing_keys(self, environment: Settings, now: datetime) -> AdminSettings:
        """Read the table; write the .env value for every key it does not have yet."""
        defaults = defaults_from_environment(environment).model_dump(mode="json")
        with self._database.transaction() as connection:
            stored = settings_repo.load_settings_values(connection)
            now_values = {}
            for key, default in defaults.items():
                if key not in stored:
                    settings_repo.save_setting(connection, key, default, now)
                    stored[key] = default
                now_values[key] = stored[key]
        return AdminSettings.model_validate(now_values)

    @property
    def current(self) -> AdminSettings:
        with self._lock:
            return self._current

    @property
    def inbox_hold_bytes(self) -> int:
        return int(self.current.inbox_hold_gb * BYTES_PER_GB)

    @property
    def proxy_evict_min_free_bytes(self) -> int:
        return int(self.current.proxy_evict_min_free_gb * BYTES_PER_GB)

    @property
    def discard_grace(self) -> timedelta:
        return timedelta(hours=self.current.discard_grace_hours)

    @property
    def outbox_keep_unarchived(self) -> timedelta:
        return timedelta(hours=self.current.outbox_keep_unarchived_hours)

    @property
    def originals_keep(self) -> timedelta:
        return timedelta(hours=self.current.originals_keep_hours)

    @property
    def originals_cap_bytes(self) -> int:
        return int(self.current.originals_cap_gb * BYTES_PER_GB)

    def update(
        self, actor: AdminActor, request: UpdateSettingsRequest, now: datetime
    ) -> AdminSettings:
        """Validate and store the changed values; records one audit event."""
        changes = request.model_dump(exclude_unset=True, exclude_none=True, mode="json")
        for key in ("installer_download_url", "results_api_base_url"):
            if key in changes:
                url = changes[key].strip()
                if url and not url.startswith(("https://", "http://")):
                    raise validation_error(f"{key} must start with https://")
                changes[key] = url
        if changes.get("results_api_base_url") == "":
            raise validation_error("results_api_base_url cannot be empty")
        if "welcome_message" in changes:
            message = normalize_welcome_message(changes["welcome_message"])
            if len(message) > MAX_WELCOME_MESSAGE_LENGTH:
                raise validation_error(
                    f"welcome_message must be at most {MAX_WELCOME_MESSAGE_LENGTH} characters",
                    max_length=MAX_WELCOME_MESSAGE_LENGTH,
                )
            changes["welcome_message"] = message
        if "vertical_video_message" in changes:
            vertical_message = normalize_vertical_video_message(changes["vertical_video_message"])
            if len(vertical_message) > MAX_VERTICAL_VIDEO_MESSAGE_LENGTH:
                raise validation_error(
                    "vertical_video_message must be at most"
                    f" {MAX_VERTICAL_VIDEO_MESSAGE_LENGTH} characters",
                    max_length=MAX_VERTICAL_VIDEO_MESSAGE_LENGTH,
                )
            changes["vertical_video_message"] = vertical_message
        with self._lock:
            before = self._current
            after = AdminSettings.model_validate(before.model_dump(mode="json") | changes)
            if after.inbox_hold_gb > self._inbox_cap_gb:
                raise validation_error(
                    f"inbox_hold_gb must not be above the hard cap BVP_INBOX_CAP_GB"
                    f" ({self._inbox_cap_gb:g} GB)",
                    inbox_cap_gb=self._inbox_cap_gb,
                )
            changed = {
                key: value
                for key, value in after.model_dump(mode="json").items()
                if value != before.model_dump(mode="json")[key]
            }
            with self._database.transaction() as connection:
                for key, value in changed.items():
                    settings_repo.save_setting(connection, key, value, now)
                record_admin_change(
                    connection,
                    now,
                    actor,
                    "settings_updated",
                    "changed: " + (", ".join(sorted(changed)) or "nothing"),
                    before={key: before.model_dump(mode="json")[key] for key in changed},
                    after=changed,
                )
            self._current = after
        return after
