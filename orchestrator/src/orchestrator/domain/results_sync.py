"""Sync the configured event's provas and teams from the Baja results API.

When: the Sync button (POST /admin/results-sync), right after the event id
changes on the Settings page, and every 6 h while an event is set (the
background runner calls sync_if_due).

Provas, keyed by the API's prova id ("END"):
  * a new id → a new active prova, slug from its name (fixed from then on);
    if a manual prova already has that clip folder (e.g. "Enduro" backfilled
    from old jobs) it is adopted instead, so its jobs keep their prova;
  * a known id with a new name → renamed, and its jobs relabelled (slug kept);
  * an id the API no longer lists → made inactive, never deleted (jobs may
    use it); it becomes active again if the API lists it again.
  Synced provas are ordered as the API lists them, before the manual ones.

Teams, keyed by car number:
  * the API's values replace the stored ones; an admin's correction is an
    override in its own columns and is kept;
  * a car the API no longer lists is marked "not in the event", never deleted.

Each sync writes one event (kind results_synced) and keeps its report as
the "last sync" shown on the Settings, Provas and Teams pages.
"""

import logging
import sqlite3
from datetime import datetime, timedelta

from orchestrator.clients.results_api import ApiProva, ApiTeam, ResultsApiError
from orchestrator.context import OrchestratorContext
from orchestrator.domain.audit import AdminActor
from orchestrator.errors import ApiError, validation_error
from orchestrator.repositories import app_settings as settings_repo
from orchestrator.repositories import catalog as catalog_repo
from orchestrator.repositories import events as events_repo
from orchestrator.repositories import teams as teams_repo
from shared.layout import slugify_prova
from shared.models import CatalogSource, ErrorCode, ResultsSyncReport, Team

logger = logging.getLogger(__name__)

SYNC_INTERVAL = timedelta(hours=6)
# After a failed sync, try again this soon instead of waiting 6 h.
RETRY_AFTER_FAILURE = timedelta(minutes=15)
LAST_SYNC_KEY = "results_sync_last"


def last_report(context: OrchestratorContext) -> ResultsSyncReport | None:
    with context.database.transaction() as connection:
        stored = settings_repo.load_settings_values(connection).get(LAST_SYNC_KEY)
    return ResultsSyncReport.model_validate(stored) if stored else None


def sync_now(context: OrchestratorContext, actor: AdminActor | None) -> ResultsSyncReport:
    """Fetch and apply. Never raises for an API failure: the report says what happened.

    `actor` None means the background schedule (the event's actor is "system").
    """
    settings = context.admin_settings.current
    if not settings.event_id:
        raise validation_error("set the event id on the Settings page first")
    event_id, now = settings.event_id, context.clock.now()
    who = actor.event_actor if actor else events_repo.SYSTEM_ACTOR
    try:
        # Network first, outside any transaction.
        api_provas = context.results_api.event_provas(settings.results_api_base_url, event_id)
        api_teams = context.results_api.event_teams(settings.results_api_base_url, event_id)
    except ResultsApiError as error:
        logger.warning("results API sync for %s failed: %s", event_id, error)
        report = ResultsSyncReport(
            event_id=event_id, at=now, ok=False, error=str(error), error_code=error.code
        )
        _store(context, report, who)
        return report
    with context.database.transaction() as connection:
        added, renamed, deactivated = _sync_provas(connection, api_provas, now)
        teams_added, teams_updated, teams_missing = _sync_teams(connection, api_teams, now)
    report = ResultsSyncReport(
        event_id=event_id,
        at=now,
        ok=True,
        provas_added=added,
        provas_renamed=renamed,
        provas_deactivated=deactivated,
        teams_added=teams_added,
        teams_updated=teams_updated,
        teams_deactivated=teams_missing,
    )
    _store(context, report, who)
    return report


def sync_or_raise(context: OrchestratorContext, actor: AdminActor) -> ResultsSyncReport:
    """POST /admin/results-sync: like sync_now, but a failure is an error answer."""
    report = sync_now(context, actor)
    if report.ok:
        return report
    details = report.model_dump(mode="json")
    if report.error_code == "not_found":  # a wrong event id: the admin can fix it
        raise ApiError(422, ErrorCode.VALIDATION_ERROR, report.error or "event not found", details)
    raise ApiError(503, ErrorCode.UPSTREAM_ERROR, f"results API: {report.error}", details)


def sync_if_due(context: OrchestratorContext) -> ResultsSyncReport | None:
    """The background schedule: sync when an event is set and the last sync is old enough."""
    event_id = context.admin_settings.current.event_id
    if not event_id:
        return None
    previous = last_report(context)
    if previous is not None and previous.event_id == event_id:
        wait = SYNC_INTERVAL if previous.ok else RETRY_AFTER_FAILURE
        if context.clock.now() - previous.at < wait:
            return None
    return sync_now(context, actor=None)


def _store(context: OrchestratorContext, report: ResultsSyncReport, actor: str) -> None:
    with context.database.transaction() as connection:
        settings_repo.save_setting(
            connection, LAST_SYNC_KEY, report.model_dump(mode="json"), report.at
        )
        if report.ok:
            reason = (
                f"{report.event_id}: provas +{report.provas_added} renamed {report.provas_renamed}"
                f" inactive {report.provas_deactivated}; teams +{report.teams_added}"
                f" updated {report.teams_updated} gone {report.teams_deactivated}"
            )
        else:
            reason = f"{report.event_id}: failed ({report.error_code}): {report.error}"
        events_repo.record_event(
            connection,
            report.at,
            actor=actor,
            kind="results_synced",
            reason=reason,
            details={"before": None, "after": report.model_dump(mode="json")},
        )


# --- Provas ---


def _free_name(connection: sqlite3.Connection, name: str, prova_id: str, own_row_id=None) -> str:
    """The API's name, or "Name (ID)" if another prova already uses that name."""
    taken = catalog_repo.find_prova_row_by_name(connection, name)
    return name if taken is None or taken["id"] == own_row_id else f"{name} ({prova_id})"


def _rename(connection: sqlite3.Connection, row: sqlite3.Row, new_name: str) -> None:
    catalog_repo.rename_prova_on_jobs(connection, row["name"], new_name)


def _sync_provas(
    connection: sqlite3.Connection, api_provas: list[ApiProva], now: datetime
) -> tuple[int, int, int]:
    added = renamed = deactivated = 0
    for position, prova in enumerate(api_provas, start=1):
        row = catalog_repo.find_synced_prova_row(connection, prova.prova_id)
        if row is None:
            try:
                slug = slugify_prova(prova.name)
            except ValueError:
                logger.warning("results API prova %r has no letters or digits", prova.name)
                continue
            same_folder = catalog_repo.find_prova_row_by_slug(connection, slug)
            if same_folder is not None and same_folder["source"] == CatalogSource.MANUAL:
                # Adopt it: jobs that use it stay attached, the folder stays.
                name = _free_name(connection, prova.name, prova.prova_id, same_folder["id"])
                if name != same_folder["name"]:
                    _rename(connection, same_folder, name)
                catalog_repo.update_prova(
                    connection,
                    same_folder["id"],
                    now,
                    {
                        "name": name,
                        "source": str(CatalogSource.RESULTS_API),
                        "external_id": prova.prova_id,
                        "display_order": position,
                        "in_source": 1,
                    },
                )
                added += 1
                continue
            if same_folder is not None:  # another synced prova has that folder
                slug = f"{slug}-{prova.prova_id.lower()}"
            catalog_repo.insert_prova(
                connection,
                _free_name(connection, prova.name, prova.prova_id),
                slug,
                now,
                source=CatalogSource.RESULTS_API,
                external_id=prova.prova_id,
                display_order=position,
            )
            added += 1
            continue
        changes: dict = {"display_order": position, "in_source": 1}
        if not row["in_source"]:
            changes["active"] = 1  # the sync had deactivated it; it is back
        name = _free_name(connection, prova.name, prova.prova_id, row["id"])
        if name != row["name"]:
            _rename(connection, row, name)
            changes["name"] = name
            renamed += 1
        catalog_repo.update_prova(connection, row["id"], now, changes)
    listed = {prova.prova_id for prova in api_provas}
    for row in catalog_repo.list_synced_prova_rows(connection):
        if row["external_id"] not in listed and row["in_source"]:
            catalog_repo.update_prova(connection, row["id"], now, {"in_source": 0, "active": 0})
            deactivated += 1
    return added, renamed, deactivated


# --- Teams ---


def _sync_teams(
    connection: sqlite3.Connection, api_teams: list[ApiTeam], now: datetime
) -> tuple[int, int, int]:
    added = updated = 0
    for api_team in api_teams:
        team = Team(
            car_number=api_team.car_number,
            team_name=api_team.name,
            short_name=api_team.short_name,
            school=api_team.school,
        )
        before = teams_repo.get_team_row(connection, team.car_number)
        if before is None:
            added += 1
        elif (
            before["source"] != CatalogSource.RESULTS_API
            or not before["in_source"]
            or (before["team_name"], before["short_name"], before["school"])
            != (team.team_name, team.short_name, team.school)
        ):
            updated += 1
        teams_repo.save_synced_team(connection, team, now)
    listed = {team.car_number for team in api_teams}
    missing = teams_repo.mark_synced_teams_missing(connection, listed, now)
    return added, updated, missing
