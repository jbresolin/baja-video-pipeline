"""Wait for the official result before rendering (LIVESTACK-62, design §38).

Judges often enter a passagem's result a few minutes after the reviewer
has the video ready. "Pronto" (or "Salvar e reprocessar") with "Esperar o
resultado oficial" ticked makes the job `ready` as always, plus a *hold*:
jobs.result_hold_until. While it is set, task_rules offers the render to
no worker. The hold ends, and the render is offered at once, on the first
of these:

    the result is in    the result line (when it is shown) and, when a
                        clock is drawn, its official time are fetched;
    the deadline        result_hold_until passes: the clip is rendered with
                        whatever there is, and §32 flags it when the result
                        arrives later;
    "Renderizar agora"  a reviewer does not want to wait (render_now);
    nothing to wait for the review changed (result toggle and clock off,
                        no largada) or the prova lost its configuration:
                        the hold is dropped.

release_held_jobs() runs right after each successful results fetch
(pass_results_fetch.fetch_table), so a result releases its job within one
fetch, and on the lease sweeper's timer (every 5 s) for the deadlines and
for configuration changes. A reviewer's save on a held job checks it at
once. Holds are a column of the job, so a restart keeps them.
"""

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta

from orchestrator.context import OrchestratorContext
from orchestrator.db.database import from_db_time, to_db_time
from orchestrator.domain.job_view import load_job, require_job_row
from orchestrator.domain.pass_clock import pass_clock_for_job
from orchestrator.domain.pass_results import pass_result_for_job
from orchestrator.domain.render_features import process_waiting_reason
from orchestrator.repositories import events as events_repo
from orchestrator.repositories import jobs as jobs_repo
from orchestrator.repositories.events import SYSTEM_ACTOR
from shared.models import Job, JobState, ReviewAction, ReviewUpdate

# Job.waiting_reason of a held job: its render waits for the result, not for
# a worker (the review UI shows it in pt-BR, with Job.result_hold_until).
WAITING_FOR_OFFICIAL_RESULT = "waiting for the official result of the passagem before rendering"


@dataclass(frozen=True)
class WaitStatus:
    """What a render of a job would draw from the results API, and whether it is fetched."""

    # "Mostrar resultado da passagem" is in force for a prova with a result line.
    waits_for_result: bool
    # A clock would be drawn: the prova has a clock block, "Mostrar
    # cronômetro" is in force and the largada is marked.
    waits_for_clock: bool
    # The result line's text is known (from the last good answer fetched).
    result_is_in: bool
    # The clock's official time is known.
    clock_is_in: bool

    @property
    def nothing_to_wait_for(self) -> bool:
        """Neither a result line nor a clock would be drawn: holding is pointless."""
        return not self.waits_for_result and not self.waits_for_clock

    @property
    def everything_is_in(self) -> bool:
        """All that would be drawn is fetched (vacuously true with nothing to wait for)."""
        result_done = self.result_is_in or not self.waits_for_result
        clock_done = self.clock_is_in or not self.waits_for_clock
        return result_done and clock_done

    def waited_for_names(self) -> list[str]:
        """For the event's details: ["result"], ["clock"] or both."""
        names = []
        if self.waits_for_result:
            names.append("result")
        if self.waits_for_clock:
            names.append("clock")
        return names


def wait_status(connection: sqlite3.Connection, job_row) -> WaitStatus:
    """The job's WaitStatus now, from the same functions the render task uses."""
    result = pass_result_for_job(connection, job_row)
    clock = pass_clock_for_job(connection, job_row)
    return WaitStatus(
        waits_for_result=result.configured and result.show,
        waits_for_clock=clock.configured and clock.show and clock.largada_s is not None,
        result_is_in=result.text is not None,
        clock_is_in=clock.official_units is not None,
    )


def is_held(job_row) -> bool:
    """The job is ready and its render waits for the official result."""
    return job_row["state"] == JobState.READY and job_row["result_hold_until"] is not None


def hold_for_result(
    connection: sqlite3.Connection, job_row, now: datetime, minutes: int, actor: str
) -> bool:
    """After "Pronto" or "Salvar e reprocessar" with the box ticked: hold the render.

    No hold (the render is offered at once, as without the box) when a
    render would draw nothing from the results API, or when all of it is
    already fetched: waiting could not change the clip. True when held.
    """
    status = wait_status(connection, job_row)
    if status.nothing_to_wait_for or status.everything_is_in:
        return False
    until = now + timedelta(minutes=minutes)
    jobs_repo.update_job(
        connection,
        job_row["id"],
        now,
        {"result_hold_until": to_db_time(until), "waiting_reason": WAITING_FOR_OFFICIAL_RESULT},
    )
    events_repo.record_event(
        connection,
        now,
        actor=actor,
        kind="result_hold_started",
        job_id=job_row["id"],
        details={"until": until.isoformat(), "waits_for": status.waited_for_names()},
    )
    return True


def _release(
    connection: sqlite3.Connection,
    job_row,
    now: datetime,
    *,
    actor: str,
    kind: str,
    reason: str | None = None,
) -> None:
    """End the hold, so the render is offered at once, and record why.

    The waiting reason becomes the one of any ready job (a holder online,
    one that can render it), or none.
    """
    job_id = job_row["id"]
    jobs_repo.update_job(connection, job_id, now, {"result_hold_until": None})
    released_row = jobs_repo.get_job_row(connection, job_id)
    waiting = process_waiting_reason(connection, released_row, now)
    jobs_repo.update_job(connection, job_id, now, {"waiting_reason": waiting})
    events_repo.record_event(connection, now, actor=actor, kind=kind, job_id=job_id, reason=reason)


def check_held_job(connection: sqlite3.Connection, job_row, now: datetime) -> bool:
    """Release one held job when its hold is over; True when it was released.

    The order matters: a result that arrived in the same minute as the
    deadline counts as arrived, which is the better story for the log.
    """
    if not is_held(job_row):
        return False
    status = wait_status(connection, job_row)
    if status.nothing_to_wait_for:
        _release(
            connection,
            job_row,
            now,
            actor=SYSTEM_ACTOR,
            kind="result_hold_dropped",
            reason="nothing to wait for: neither a result line nor a clock would be drawn",
        )
        return True
    if status.everything_is_in:
        _release(
            connection,
            job_row,
            now,
            actor=SYSTEM_ACTOR,
            kind="result_hold_released_result_arrived",
        )
        return True
    if now >= from_db_time(job_row["result_hold_until"]):
        _release(
            connection,
            job_row,
            now,
            actor=SYSTEM_ACTOR,
            kind="result_hold_released_deadline_passed",
        )
        return True
    return False


def release_held_jobs(connection: sqlite3.Connection, now: datetime) -> list[str]:
    """Check every held job (after a fetch, and on the sweeper's timer). The ids released."""
    released = []
    for job_row in jobs_repo.list_held_job_rows(connection):
        if check_held_job(connection, job_row, now):
            released.append(job_row["id"])
    return released


def apply_result_wait_choice(
    connection: sqlite3.Connection,
    job_row,
    update: ReviewUpdate,
    minutes: int,
    actor: str,
    now: datetime,
) -> None:
    """After a reviewer's PATCH …/review (domain/review.py), with the job as it is now.

    "Pronto" and "Salvar e reprocessar" with the box ticked hold the render
    (unless there is nothing to wait for). A save on a held job checks the
    hold at once: a changed passagem may have its result already, and
    switching the result and the clock off leaves nothing to wait for.
    """
    if update.action in (ReviewAction.READY, ReviewAction.REPROCESS):
        if update.wait_for_result:
            hold_for_result(connection, job_row, now, minutes, actor)
        return
    if update.action == ReviewAction.SAVE:
        check_held_job(connection, job_row, now)


def waiting_reason_for_ready_job(connection: sqlite3.Connection, job_row, now: datetime):
    """Why a ready job's render is not starting: the result for a held job
    (never "waiting for a worker"), else what render_features says."""
    if is_held(job_row):
        return WAITING_FOR_OFFICIAL_RESULT
    return process_waiting_reason(connection, job_row, now)


def render_now(context: OrchestratorContext, reviewer: str, job_id: str) -> Job:
    """ "Renderizar agora": end the hold at once (POST /jobs/{id}/render-now).

    Nothing to do for a job that is not held (released a moment ago, or
    never held): it answers the job as it is, so a second click is harmless.
    Not a review change, so the job's version stays (like regenerate-proxy).
    """
    with context.database.transaction() as connection:
        now = context.clock.now()
        job_row = require_job_row(connection, job_id)
        if is_held(job_row):
            _release(
                connection,
                job_row,
                now,
                actor=events_repo.reviewer_actor(reviewer),
                kind="result_hold_released_by_reviewer",
            )
        return load_job(connection, job_id, context.layout, now)
