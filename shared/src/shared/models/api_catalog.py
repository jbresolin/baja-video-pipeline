"""Provas and teams: the lists the review form picks from (LIVESTACK-37)."""

from datetime import datetime

from pydantic import Field

from shared.models.common import CarNumber, ReadByClients, ReceivedByServer
from shared.models.enums import CatalogSource

# --- Provas ---


class Prova(ReadByClients):
    """Read by clients: unknown fields are ignored."""

    id: int = Field(ge=1)
    # What reviewers see and Job.prova holds, e.g. "Suspensão".
    name: str
    # Folder name of its clips on the studio box, e.g. "suspensao". Fixed
    # when the prova is created: a rename keeps it (docs/api.md §8.1).
    slug: str
    # Position in the review form's list, 1 = first.
    display_order: int = Field(ge=1)
    # Inactive provas are hidden from the review form but stay valid for
    # jobs that already have them. A synced prova the API stopped listing is
    # made inactive (never deleted).
    active: bool
    source: CatalogSource = CatalogSource.MANUAL
    # The results API's prova id ("END") for a synced prova, else None.
    external_id: str | None = None


class ProvasResponse(ReadByClients):
    """Read by clients: unknown fields are ignored."""

    # Synced provas first (in the API's order), then manual ones (in the
    # admin's order); inactive ones included.
    provas: list[Prova]


class CreateProvaRequest(ReceivedByServer):
    """Received by the orchestrator: unknown fields are refused."""

    name: str = Field(min_length=1, max_length=64)


class UpdateProvaRequest(ReceivedByServer):
    """PATCH /admin/provas/{prova_id}. Fields left out keep their value.

    Received by the orchestrator: unknown fields are refused.
    """

    # A rename relabels every job that had the old name; the slug stays.
    # Synced provas take their name from the API: renaming one is a 409.
    name: str | None = Field(default=None, min_length=1, max_length=64)
    active: bool | None = None


class ReorderProvasRequest(ReceivedByServer):
    """Received by the orchestrator: unknown fields are refused."""

    # Every prova id, in the new order.
    prova_ids: list[int] = Field(min_length=1)


# --- Teams ---


class TeamRow(ReadByClients):
    """A row of the admin Teams page: what lookups answer, and where it came from.

    Read by clients: unknown fields are ignored.
    """

    car_number: CarNumber
    # What GET /teams/{car} answers: the admin's correction if any, else the API's.
    team_name: str
    short_name: str | None = None
    school: str | None = None
    school_short: str | None = None
    source: CatalogSource
    # Synced teams only: False when the last sync no longer listed the car.
    in_source: bool = True
    # True when an admin corrected a synced team (kept across syncs).
    overridden: bool = False
    # The results API's own values, for a synced team.
    synced_team_name: str | None = None
    synced_short_name: str | None = None
    synced_school: str | None = None
    synced_school_short: str | None = None


class TeamsResponse(ReadByClients):
    """Read by clients: unknown fields are ignored."""

    # Sorted by car number.
    teams: list[TeamRow]


class SaveTeamRequest(ReceivedByServer):
    """PUT /admin/teams/{car_number}: add a car, edit a manual one, or correct a synced one.

    For a synced team the values are stored as a correction (override) that
    later syncs keep.

    Received by the orchestrator: unknown fields are refused.
    """

    team_name: str = Field(min_length=1, max_length=100)
    short_name: str | None = Field(default=None, max_length=60)
    school: str | None = Field(default=None, max_length=100)
    # The school's short name on the clip overlay ("UFMG").
    school_short: str | None = Field(default=None, max_length=30)


# --- Results API sync ---


class ResultsSyncReport(ReadByClients):
    """What the last sync with the Baja results API did (or why it failed).

    Read by clients: unknown fields are ignored.
    """

    event_id: str
    at: datetime
    ok: bool
    # Why it failed, e.g. "event 26XX not found in the results API", and the
    # API's error code ("not_found", "rate_limited", "unreachable", ...).
    error: str | None = None
    error_code: str | None = None
    provas_added: int = Field(default=0, ge=0)
    provas_renamed: int = Field(default=0, ge=0)
    provas_deactivated: int = Field(default=0, ge=0)
    teams_added: int = Field(default=0, ge=0)
    teams_updated: int = Field(default=0, ge=0)
    teams_deactivated: int = Field(default=0, ge=0)
