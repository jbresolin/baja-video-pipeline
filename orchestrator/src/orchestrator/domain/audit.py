"""The audit trail of admin changes (admin page, LIVESTACK-37).

Every change made through an /admin/* route is written to the existing
events log with who made it, what kind of change it was, and the record
before and after as JSON. The admin pages show the newest ones
(event_log.recent_admin_changes). This module imports nothing from the
context, so the settings store can use it.
"""

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from orchestrator.repositories import events as events_repo
from shared.models import AdminArea

# The event kinds each admin page shows under "Últimas alterações".
KINDS_BY_AREA: dict[AdminArea, list[str]] = {
    AdminArea.USERS: ["user_created", "user_updated"],
    AdminArea.CONTACTS: ["contact_created", "contact_updated", "welcome_sent", "welcome_failed"],
    AdminArea.PROVAS: ["prova_created", "prova_updated", "provas_reordered", "results_synced"],
    AdminArea.TEAMS: ["team_saved", "team_deleted", "results_synced"],
    AdminArea.ENROLLMENT: ["enrollment_token_created", "enrollment_token_revoked"],
    AdminArea.WORKERS: ["worker_revoked", "worker_renamed", "master_changed"],
    AdminArea.SETTINGS: ["settings_updated"],
}


@dataclass(frozen=True)
class AdminActor:
    """Who made an admin change: a signed-in admin, or the static token (username None)."""

    username: str | None = None

    @property
    def event_actor(self) -> str:
        if self.username is None:
            return events_repo.ADMIN_TOKEN_ACTOR
        return events_repo.admin_actor(self.username)


def record_admin_change(
    connection: sqlite3.Connection,
    now: datetime,
    actor: AdminActor,
    kind: str,
    reason: str,
    *,
    before: Any = None,
    after: Any = None,
    worker_id: str | None = None,
) -> None:
    """Append one admin change. `before`/`after` are JSON-ready (dicts, lists, None)."""
    events_repo.record_event(
        connection,
        now,
        actor=actor.event_actor,
        kind=kind,
        worker_id=worker_id,
        reason=reason,
        details={"before": before, "after": after},
    )
