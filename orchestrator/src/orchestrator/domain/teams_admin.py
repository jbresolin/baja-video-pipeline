"""Admin Teams page: the table with inline edit, add, delete, and a CSV upload."""

from orchestrator.clients.teams import parse_teams_csv
from orchestrator.context import OrchestratorContext
from orchestrator.domain.audit import AdminActor, record_admin_change
from orchestrator.errors import not_found
from orchestrator.repositories import catalog as catalog_repo
from shared.models import (
    ImportTeamsRequest,
    ImportTeamsResponse,
    SaveTeamRequest,
    Team,
    TeamsImportMode,
    TeamsResponse,
)


def _team_json(team: Team | None) -> dict | None:
    return team.model_dump(mode="json") if team is not None else None


def list_teams(context: OrchestratorContext) -> TeamsResponse:
    with context.database.transaction() as connection:
        return TeamsResponse(teams=catalog_repo.list_teams(connection))


def save_team(
    context: OrchestratorContext, actor: AdminActor, car_number: int, request: SaveTeamRequest
) -> Team:
    """Add the car, or change its team name and school (PUT is the same for both)."""
    team = Team(
        car_number=car_number,
        team_name=request.team_name.strip(),
        school=(request.school or "").strip() or None,
    )
    now = context.clock.now()
    with context.database.transaction() as connection:
        before = catalog_repo.get_team(connection, car_number)
        catalog_repo.save_team(connection, team, now)
        record_admin_change(
            connection,
            now,
            actor,
            "team_saved",
            f"car {car_number}: {team.team_name}" + (" (new)" if before is None else ""),
            before=_team_json(before),
            after=_team_json(team),
        )
    return team


def delete_team(context: OrchestratorContext, actor: AdminActor, car_number: int) -> None:
    now = context.clock.now()
    with context.database.transaction() as connection:
        before = catalog_repo.get_team(connection, car_number)
        if before is None:
            raise not_found("team")
        catalog_repo.delete_team(connection, car_number)
        record_admin_change(
            connection,
            now,
            actor,
            "team_deleted",
            f"car {car_number} ({before.team_name}) removed",
            before=_team_json(before),
        )


def import_teams(
    context: OrchestratorContext, actor: AdminActor, request: ImportTeamsRequest
) -> ImportTeamsResponse:
    """Replace the whole table with the CSV, or merge it in (the operator picks).

    In both modes a car in the file gets the file's team name and school.
    "replace" also removes every car the file does not list; "merge"
    keeps them. Lines that do not parse are skipped and reported. A file
    with no valid line never empties the table, even in "replace" mode.
    """
    parsed = parse_teams_csv(request.csv_text)
    now = context.clock.now()
    with context.database.transaction() as connection:
        count_before = catalog_repo.count_teams(connection)
        removed = 0
        if request.mode == TeamsImportMode.REPLACE and parsed.teams:
            removed = catalog_repo.delete_all_teams(connection)
        for team in parsed.teams.values():
            catalog_repo.save_team(connection, team, now)
        total = catalog_repo.count_teams(connection)
        record_admin_change(
            connection,
            now,
            actor,
            "teams_imported",
            f"CSV {request.mode}: {len(parsed.teams)} team(s) imported,"
            f" {len(parsed.problems)} line(s) skipped",
            before={"team_count": count_before},
            after={
                "team_count": total,
                "mode": str(request.mode),
                "imported": len(parsed.teams),
                "removed_first": removed,
                "problems": parsed.problems,
            },
        )
    return ImportTeamsResponse(
        imported=len(parsed.teams), problems=parsed.problems, total_teams=total
    )
