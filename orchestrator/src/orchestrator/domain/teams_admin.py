"""Admin Teams page: the synced table, plus manual corrections and additions.

Teams come from the results API (domain/results_sync.py). The admin can:

* correct a synced team: stored as an override, so later syncs keep it;
* undo that correction ("remove" on a synced team): the API's values again;
* add, edit or remove a car the API does not list (a manual team).

There is no CSV upload any more (brief amendment): the API makes it unnecessary.
"""

from orchestrator.context import OrchestratorContext
from orchestrator.domain.audit import AdminActor, record_admin_change
from orchestrator.errors import not_found
from orchestrator.repositories import teams as teams_repo
from shared.models import CatalogSource, SaveTeamRequest, Team, TeamRow, TeamsResponse


def _row_json(row) -> dict | None:
    return teams_repo.team_row_from_row(row).model_dump(mode="json") if row is not None else None


def _clean(value: str | None) -> str | None:
    return (value or "").strip() or None


def list_teams(context: OrchestratorContext) -> TeamsResponse:
    with context.database.transaction() as connection:
        rows = teams_repo.list_team_rows(connection)
    return TeamsResponse(teams=[teams_repo.team_row_from_row(row) for row in rows])


def save_team(
    context: OrchestratorContext, actor: AdminActor, car_number: int, request: SaveTeamRequest
) -> TeamRow:
    """A new car or a manual one: its values. A synced one: a correction (override)."""
    team = Team(
        car_number=car_number,
        team_name=request.team_name.strip(),
        short_name=_clean(request.short_name),
        school=_clean(request.school),
        school_short=_clean(request.school_short),
    )
    now = context.clock.now()
    with context.database.transaction() as connection:
        before = teams_repo.get_team_row(connection, car_number)
        synced = before is not None and before["source"] == CatalogSource.RESULTS_API
        if synced:
            teams_repo.set_override(connection, car_number, team, now)
            what = f"car {car_number}: corrected to {team.team_name} (kept across syncs)"
        else:
            teams_repo.save_manual_team(connection, team, now)
            what = f"car {car_number}: {team.team_name}" + (" (new)" if before is None else "")
        after = teams_repo.get_team_row(connection, car_number)
        record_admin_change(
            connection,
            now,
            actor,
            "team_saved",
            what,
            before=_row_json(before),
            after=_row_json(after),
        )
    return teams_repo.team_row_from_row(after)


def delete_team(context: OrchestratorContext, actor: AdminActor, car_number: int) -> None:
    """A manual team is removed; a synced team only loses the admin's correction."""
    now = context.clock.now()
    with context.database.transaction() as connection:
        before = teams_repo.get_team_row(connection, car_number)
        if before is None:
            raise not_found("team")
        if before["source"] == CatalogSource.RESULTS_API:
            teams_repo.set_override(connection, car_number, None, now)
            what = f"car {car_number}: correction removed, back to the results API's values"
        else:
            teams_repo.delete_team(connection, car_number)
            what = f"car {car_number} ({before['team_name']}) removed"
        after = teams_repo.get_team_row(connection, car_number)
        record_admin_change(
            connection,
            now,
            actor,
            "team_deleted",
            what,
            before=_row_json(before),
            after=_row_json(after),
        )
