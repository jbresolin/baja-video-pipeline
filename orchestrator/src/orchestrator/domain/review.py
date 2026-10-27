"""Review queue, review updates, proxy regeneration and team lookup (docs/api.md §7.1)."""

import logging
import sqlite3
from typing import Any

from orchestrator.clients.baja_app import BajaAppError
from orchestrator.context import OrchestratorContext
from orchestrator.db.database import to_db_time
from orchestrator.domain.job_view import load_job, require_job_row
from orchestrator.domain.raw_copies import drop_inbox_copy
from orchestrator.domain.transitions import move_job, revoke_job_leases
from orchestrator.errors import ApiError, illegal_transition
from orchestrator.repositories import events as events_repo
from orchestrator.repositories import jobs as jobs_repo
from orchestrator.repositories import leases as leases_repo
from orchestrator.storage import delete_file_and_partials
from shared.models import (
    ErrorCode,
    Job,
    JobState,
    ReviewAction,
    ReviewUpdate,
    TaskKind,
)
from shared.state_machine import (
    DISCARDABLE_STATES,
    REPROCESSABLE_STATES,
    RETRYABLE_STATES,
    retry_target_for,
)

logger = logging.getLogger(__name__)

REQUIRED_REVIEW_FIELDS = ("prova", "car_number", "passagem", "cut_in_s", "cut_out_s")
EDITABLE_FIELDS = ("prova", "car_number", "passagem", "show_pass", "cut_in_s", "cut_out_s")
CLEARED_CLIP_COLUMNS = {
    "clip_sha256": None,
    "clip_size_bytes": None,
    "clip_relative_path": None,
    "clip_route": None,
    "clip_rendered_by": None,
    "clip_delivered_at": None,
}
CLEARED_FAILURE_COLUMNS = {
    "failure_worker_id": None,
    "failure_task_kind": None,
    "failure_message": None,
    "failure_exit_code": None,
    "failure_at": None,
    "failed_from": None,
}


def _team_name_or_none(context: OrchestratorContext, car_number: int) -> str | None:
    """The team name for a review; a Baja app outage must not block reviewing."""
    try:
        team = context.baja_app.find_team(car_number)
    except BajaAppError as error:
        logger.warning("team lookup for car %s failed: %s", car_number, error)
        return None
    return team.team_name if team else None


def _check_action_allowed(state: JobState, action: ReviewAction) -> None:
    allowed = {
        ReviewAction.SAVE: state != JobState.DISCARDED,
        ReviewAction.READY: state == JobState.AWAITING_REVIEW,
        ReviewAction.REPROCESS: state in REPROCESSABLE_STATES,
        ReviewAction.DISCARD: state in DISCARDABLE_STATES,
        ReviewAction.RETRY: state in RETRYABLE_STATES,
    }[action]
    if not allowed:
        raise illegal_transition(state, action)


def _merged_fields(job_row: sqlite3.Row, update: ReviewUpdate) -> dict[str, Any]:
    """The review fields as they will be after the update (None in the update = keep)."""
    merged = {name: job_row[name] for name in EDITABLE_FIELDS}
    for name in EDITABLE_FIELDS:
        value = getattr(update, name)
        if value is not None:
            merged[name] = value
    merged["show_pass"] = int(bool(merged["show_pass"]))
    return merged


def _check_cuts(job_row: sqlite3.Row, merged: dict[str, Any]) -> None:
    cut_in, cut_out = merged["cut_in_s"], merged["cut_out_s"]
    if cut_in is not None and cut_out is not None and cut_out <= cut_in:
        raise ApiError(422, ErrorCode.VALIDATION_ERROR, "cut_out_s must be after cut_in_s")
    duration = job_row["duration_s"]
    if duration is not None and cut_out is not None and cut_out > duration:
        raise ApiError(
            422,
            ErrorCode.VALIDATION_ERROR,
            f"cut_out_s is past the end of the video ({duration} s)",
            {"duration_s": duration},
        )


def _check_required_fields(merged: dict[str, Any]) -> None:
    missing = [name for name in REQUIRED_REVIEW_FIELDS if merged[name] is None]
    if missing:
        raise ApiError(
            422,
            ErrorCode.MISSING_REVIEW_FIELDS,
            "these fields must be set: " + ", ".join(missing),
            {"missing": missing},
        )


def update_review(
    context: OrchestratorContext, reviewer: str, job_id: str, update: ReviewUpdate
) -> Job:
    # Network call before the transaction (cached in the client).
    team_name = None
    if update.car_number is not None:
        team_name = _team_name_or_none(context, update.car_number)

    with context.database.transaction() as connection:
        now = context.clock.now()
        job_row = require_job_row(connection, job_id)
        if job_row["version"] != update.expected_version:
            raise ApiError(
                409,
                ErrorCode.VERSION_CONFLICT,
                "the job changed since you loaded it; reload",
                {"current_version": job_row["version"]},
            )
        state = JobState(job_row["state"])
        _check_action_allowed(state, update.action)
        merged = _merged_fields(job_row, update)
        _check_cuts(job_row, merged)

        changes: dict[str, Any] = {
            **merged,
            "reviewed_by": reviewer,
            "reviewed_at": to_db_time(now),
        }
        if update.car_number is not None:
            changes["team_name"] = team_name
        actor = events_repo.reviewer_actor(reviewer)
        _apply_action(context, connection, job_row, update.action, changes, merged, actor)
        return load_job(connection, job_id)


def _apply_action(
    context: OrchestratorContext,
    connection: sqlite3.Connection,
    job_row: sqlite3.Row,
    action: ReviewAction,
    changes: dict[str, Any],
    merged: dict[str, Any],
    actor: str,
) -> None:
    now = context.clock.now()
    job_id = job_row["id"]
    if action == ReviewAction.SAVE:
        jobs_repo.update_job(connection, job_id, now, changes)
        events_repo.record_event(connection, now, actor=actor, kind="review_saved", job_id=job_id)
        return
    if action == ReviewAction.READY:
        _check_required_fields(merged)
        move_job(
            connection,
            job_row,
            JobState.READY,
            now,
            actor=actor,
            reason="reviewed",
            changes=changes,
        )
        return
    if action == ReviewAction.REPROCESS:
        _check_required_fields(merged)
        revoke_job_leases(
            connection,
            job_id,
            now,
            actor=actor,
            reason="reviewer changed the cut",
            kinds={TaskKind.PROCESS, TaskKind.DELIVER},
        )
        delete_file_and_partials(context.layout.outbox_path(job_id))
        move_job(
            connection,
            job_row,
            JobState.READY,
            now,
            actor=actor,
            reason="reprocess",
            changes={**changes, **CLEARED_CLIP_COLUMNS},
        )
        return
    if action == ReviewAction.DISCARD:
        revoke_job_leases(connection, job_id, now, actor=actor, reason="job discarded")
        delete_file_and_partials(context.layout.outbox_path(job_id))
        move_job(
            connection,
            job_row,
            JobState.DISCARDED,
            now,
            actor=actor,
            reason="discarded",
            changes={**changes, "proxy_requested": 0, "waiting_reason": None},
        )
        drop_inbox_copy(
            connection,
            context.layout,
            require_job_row(connection, job_id),
            now,
            actor=actor,
            reason="job discarded",
        )
        return
    # RETRY: back to where the job failed (claimed → ready).
    failed_from = JobState(job_row["failed_from"] or JobState.CLAIMED)
    target = retry_target_for(failed_from)
    if target == JobState.READY:
        _check_required_fields(merged)
    leases_repo.clear_task_exclusions(connection, job_id)
    move_job(
        connection,
        job_row,
        target,
        now,
        actor=actor,
        reason="retry after failure",
        changes={**changes, **CLEARED_FAILURE_COLUMNS, "waiting_reason": None},
    )
    if target == JobState.PROXY_READY:
        # The proxy exists (that is what proxy_ready means): straight back to review.
        move_job(
            connection,
            require_job_row(connection, job_id),
            JobState.AWAITING_REVIEW,
            now,
            actor="system",
            reason="proxy ready: queued for review",
        )
