"""Provas and teams: the lists the review form picks from (LIVESTACK-37)."""

from pydantic import Field

from shared.models.api_review import Team
from shared.models.common import ApiModel
from shared.models.enums import TeamsImportMode

# --- Provas ---


class Prova(ApiModel):
    id: int = Field(ge=1)
    # What reviewers see and Job.prova holds, e.g. "Suspensão".
    name: str
    # Folder name of its clips on the studio box, e.g. "suspensao". Fixed
    # when the prova is created: a rename keeps it (docs/api.md §8.1).
    slug: str
    # Position in the review form's list, 1 = first.
    display_order: int = Field(ge=1)
    # Inactive provas are hidden from the review form but stay valid for
    # jobs that already have them.
    active: bool


class ProvasResponse(ApiModel):
    # In display order, inactive ones included.
    provas: list[Prova]


class CreateProvaRequest(ApiModel):
    name: str = Field(min_length=1, max_length=64)


class UpdateProvaRequest(ApiModel):
    """PATCH /admin/provas/{prova_id}. Fields left out keep their value."""

    # A rename relabels every job that had the old name; the slug stays.
    name: str | None = Field(default=None, min_length=1, max_length=64)
    active: bool | None = None


class ReorderProvasRequest(ApiModel):
    # Every prova id, in the new order.
    prova_ids: list[int] = Field(min_length=1)


# --- Teams ---


class TeamsResponse(ApiModel):
    # Sorted by car number.
    teams: list[Team]


class SaveTeamRequest(ApiModel):
    """PUT /admin/teams/{car_number}: add the car or change its team."""

    team_name: str = Field(min_length=1, max_length=100)
    school: str | None = Field(default=None, max_length=100)


class ImportTeamsRequest(ApiModel):
    """POST /admin/teams/import: the text of a `car_number,team_name,school` CSV."""

    csv_text: str = Field(max_length=1_000_000)
    mode: TeamsImportMode


class ImportTeamsResponse(ApiModel):
    # Lines that became a team.
    imported: int = Field(ge=0)
    # One message per line that was skipped, e.g. "line 4: car_number is not a number".
    problems: list[str]
    # Teams in the table after the import.
    total_teams: int = Field(ge=0)
