"""One-time imports at startup, from the files the admin page replaces.

1. Users: on a start with an EMPTY users table, every reviewer in
   BVP_REVIEWERS_FILE (reviewers.txt) becomes a user with role reviewer;
   their bcrypt hashes are reused as they are, so passwords do not change.
   The user named in BVP_BOOTSTRAP_ADMIN becomes an admin. Once the table
   has users the file is ignored (a log line says so on every start).
2. Teams: BVP_TEAMS_FILE is imported once into an empty teams table, then
   ignored, even if the admin later deletes every team.
3. Provas: every distinct prova already on a job becomes a prova entry,
   once. Names that would share a clip folder (say "Enduro" and "enduro")
   become one prova named after the first one used, and the other jobs are
   relabelled to that name, so every job keeps a prova from the list.
4. Recording times (LIVESTACK-45): migration 0011 gives every older job
   its creation as recording time; once, every job whose probe report is
   already stored gets the raw's creation_time instead, when plausible.
5. Picture sizes (LIVESTACK-50): once, every job whose probe report is
   already stored gets its display size (after the rotation note), so the
   queue marks older vertical videos too. No bot reply for those.

Each import runs in its own transaction and logs what it did.
"""

import logging
import sqlite3
from datetime import datetime
from pathlib import Path

from orchestrator.clients.teams import parse_teams_csv
from orchestrator.config import Settings
from orchestrator.db.database import Database
from orchestrator.domain.media_info import recorded_at_columns_for
from orchestrator.repositories import app_settings as settings_repo
from orchestrator.repositories import catalog as catalog_repo
from orchestrator.repositories import events as events_repo
from orchestrator.repositories import jobs as jobs_repo
from orchestrator.repositories import teams as teams_repo
from orchestrator.repositories import users as users_repo
from shared.framing import display_size_from_probe
from shared.layout import slugify_prova
from shared.models import ProbeReport, UserRole
from shared.reviewers_file import ReviewersFileError, load_reviewers_file

logger = logging.getLogger(__name__)

TEAMS_IMPORT = "teams_file"
PROVAS_BACKFILL = "prova_backfill"
RECORDED_AT_BACKFILL = "recorded_at_backfill"
DISPLAY_SIZE_BACKFILL = "display_size_backfill"


def run_first_start_imports(database: Database, settings: Settings, now: datetime) -> None:
    import_reviewers_file(database, settings.reviewers_file, settings.bootstrap_admin, now)
    import_teams_file(database, settings.teams_file, now)
    backfill_provas(database, now)
    backfill_recorded_at(database, now)
    backfill_display_size(database, now)


def import_reviewers_file(
    database: Database, path: Path, bootstrap_admin: str, now: datetime
) -> int:
    """Users from reviewers.txt into an empty users table; returns how many were added."""
    with database.transaction() as connection:
        if users_repo.count_users(connection) > 0:
            if path.exists():
                logger.info(
                    "reviewers file %s is ignored: users live in the database"
                    " (admin page, Usuários)",
                    path,
                )
            return 0
        if not path.exists():
            logger.warning(
                "no users yet and no reviewers file at %s: create the first admin with"
                " POST /admin/users and the admin token (infra/vps/README.md §4)",
                path,
            )
            return 0
        try:
            reviewers = load_reviewers_file(path)
        except (OSError, ReviewersFileError) as error:
            # Refuse to start: a half-imported file would lock reviewers out.
            raise RuntimeError(f"cannot import {path}: {error}") from error
        for username, password_hash in reviewers.items():
            role = UserRole.ADMIN if username == bootstrap_admin else UserRole.REVIEWER
            users_repo.insert_user(connection, username, password_hash, role, now)
        if bootstrap_admin and bootstrap_admin not in reviewers:
            logger.warning(
                "BVP_BOOTSTRAP_ADMIN=%s is not in %s: no admin was made", bootstrap_admin, path
            )
        events_repo.record_event(
            connection,
            now,
            actor=events_repo.SYSTEM_ACTOR,
            kind="users_imported",
            reason=f"{len(reviewers)} user(s) from {path.name}; admin: {bootstrap_admin or 'none'}",
        )
    logger.info(
        "imported %d user(s) from %s; from now on the file is ignored", len(reviewers), path
    )
    return len(reviewers)


def import_teams_file(database: Database, path: Path | None, now: datetime) -> int:
    """BVP_TEAMS_FILE into an empty teams table, once; returns how many teams were added."""
    with database.transaction() as connection:
        if settings_repo.import_was_done(connection, TEAMS_IMPORT):
            if path is not None and path.exists():
                logger.info("teams file %s is ignored: teams live in the database", path)
            return 0
        if path is None or not path.exists():
            # Nothing to import yet; try again on the next start.
            return 0
        if teams_repo.count_teams(connection) > 0:
            settings_repo.mark_import_done(connection, TEAMS_IMPORT, now, "table not empty")
            return 0
        parsed = parse_teams_csv(path.read_text(encoding="utf-8-sig"))
        for team in parsed.teams.values():
            teams_repo.save_manual_team(connection, team, now)
        for problem in parsed.problems:
            logger.warning("teams file %s: %s (skipped)", path, problem)
        detail = f"{len(parsed.teams)} team(s) from {path}"
        settings_repo.mark_import_done(connection, TEAMS_IMPORT, now, detail)
        events_repo.record_event(
            connection,
            now,
            actor=events_repo.SYSTEM_ACTOR,
            kind="teams_file_imported",
            reason=detail,
        )
    logger.info("imported %s; from now on the file is ignored", detail)
    return len(parsed.teams)


def backfill_provas(database: Database, now: datetime) -> int:
    """A prova entry for every distinct prova on a job, once; returns how many were added."""
    with database.transaction() as connection:
        if settings_repo.import_was_done(connection, PROVAS_BACKFILL):
            return 0
        added = _add_provas_used_by_jobs(connection, now)
        settings_repo.mark_import_done(
            connection, PROVAS_BACKFILL, now, f"{added} prova(s) from existing jobs"
        )
    if added:
        logger.info("added %d prova(s) used by existing jobs to the provas list", added)
    return added


def _add_provas_used_by_jobs(connection: sqlite3.Connection, now: datetime) -> int:
    added = 0
    for name in catalog_repo.distinct_job_provas(connection):
        try:
            slug = slugify_prova(name)
        except ValueError:
            logger.warning("prova %r on existing jobs has no letters or digits; skipped", name)
            continue
        if catalog_repo.find_prova_row_by_name(connection, name) is not None:
            continue
        same_folder = catalog_repo.find_prova_row_by_slug(connection, slug)
        if same_folder is not None:
            relabelled = catalog_repo.rename_prova_on_jobs(connection, name, same_folder["name"])
            logger.info(
                "prova %r shares the folder %r with %r: %d job(s) relabelled",
                name,
                slug,
                same_folder["name"],
                relabelled,
            )
            continue
        catalog_repo.insert_prova(connection, name, slug, now)
        added += 1
    return added


def backfill_recorded_at(database: Database, now: datetime) -> int:
    """Recording times from the probe reports already stored, once; returns how many changed."""
    with database.transaction() as connection:
        if settings_repo.import_was_done(connection, RECORDED_AT_BACKFILL):
            return 0
        changed = 0
        for row in jobs_repo.list_job_rows_with_media_info(connection):
            report = ProbeReport.model_validate_json(row["media_info_json"])
            columns = recorded_at_columns_for(row, report)
            if (columns["recorded_at"], columns["recorded_at_source"]) != (
                row["recorded_at"],
                row["recorded_at_source"],
            ):
                jobs_repo.set_recorded_at(connection, row["id"], columns)
                changed += 1
        settings_repo.mark_import_done(
            connection, RECORDED_AT_BACKFILL, now, f"{changed} job(s) from their probe report"
        )
    if changed:
        logger.info("recording time read from the stored probe report of %d job(s)", changed)
    return changed


def backfill_display_size(database: Database, now: datetime) -> int:
    """Picture sizes from the probe reports already stored, once; returns how many were set."""
    with database.transaction() as connection:
        if settings_repo.import_was_done(connection, DISPLAY_SIZE_BACKFILL):
            return 0
        changed = 0
        for row in jobs_repo.list_job_rows_with_media_info(connection):
            report = ProbeReport.model_validate_json(row["media_info_json"])
            size = display_size_from_probe(report.streams)
            if size is not None and size != (row["display_width"], row["display_height"]):
                jobs_repo.set_display_size(connection, row["id"], *size)
                changed += 1
        settings_repo.mark_import_done(
            connection, DISPLAY_SIZE_BACKFILL, now, f"{changed} job(s) from their probe report"
        )
    if changed:
        logger.info("picture size read from the stored probe report of %d job(s)", changed)
    return changed
