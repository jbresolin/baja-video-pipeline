"""The one place where a job changes state, and the lease helpers that go with it.

Every state change goes through move_job(), which (1) asks
shared.state_machine whether the move is legal, (2) updates the row and
(3) appends an event saying who moved it and why. Nothing else in the
orchestrator writes jobs.state.
"""

import sqlite3
from datetime import datetime, timedelta
from typing import Any

from orchestrator.db.database import to_db_time
from orchestrator.repositories import events as events_repo
from orchestrator.repositories import jobs as jobs_repo
from orchestrator.repositories import leases as leases_repo
from shared.models import HolderStatus, JobState, Lease, TaskKind
from shared.protocol import LEASE_TTL_SECONDS
from shared.state_machine import validate_transition

# Entering one of these stamps finished_at (proxy eviction goes oldest first).
FINISHED_STATES = frozenset({JobState.DONE, JobState.DISCARDED})


def move_job(
    connection: sqlite3.Connection,
    job_row: sqlite3.Row,
    to_state: JobState,
    now: datetime,
    *,
    actor: str,
    reason: str,
    changes: dict[str, Any] | None = None,
) -> None:
    """Move one job to `to_state`, plus any other column `changes`, as one update.

    Raises shared.state_machine.IllegalTransitionError for an illegal move.
    """
    from_state = JobState(job_row["state"])
    validate_transition(from_state, to_state)
    all_changes = dict(changes or {})
    all_changes["state"] = str(to_state)
    if to_state in FINISHED_STATES:
        all_changes["finished_at"] = to_db_time(now)
    jobs_repo.update_job(connection, job_row["id"], now, all_changes)
    events_repo.record_event(
        connection,
        now,
        actor=actor,
        kind="state_changed",
        job_id=job_row["id"],
        from_state=str(from_state),
        to_state=str(to_state),
        reason=reason,
    )


def end_lease(
    connection: sqlite3.Connection,
    lease: Lease,
    now: datetime,
    *,
    actor: str,
    reason: str,
    kind: str = "lease_ended",
) -> None:
    """Delete a lease row and log why it ended."""
    leases_repo.delete_lease(connection, lease.lease_id)
    events_repo.record_event(
        connection,
        now,
        actor=actor,
        kind=kind,
        job_id=lease.job_id,
        worker_id=lease.worker_id,
        reason=f"{lease.task_kind} lease {lease.lease_id}: {reason}",
    )


def revoke_job_leases(
    connection: sqlite3.Connection,
    job_id: str,
    now: datetime,
    *,
    actor: str,
    reason: str,
    kinds: set[TaskKind] | None = None,
) -> list[Lease]:
    """End every lease on a job (or only those of `kinds`). The workers see `lost`.

    A revoked download_raw lease also drops its unconfirmed holder row, so
    the slot can be offered again.
    """
    revoked = []
    for lease in leases_repo.list_leases_for_job(connection, job_id):
        if kinds is not None and lease.task_kind not in kinds:
            continue
        end_lease(connection, lease, now, actor=actor, reason=reason, kind="lease_revoked")
        if lease.task_kind == TaskKind.DOWNLOAD_RAW:
            drop_unconfirmed_holder(connection, job_id, lease.worker_id)
        revoked.append(lease)
    return revoked


def drop_unconfirmed_holder(connection: sqlite3.Connection, job_id: str, worker_id: str) -> None:
    for holder in leases_repo.list_holders(connection, job_id):
        if holder.worker_id == worker_id and holder.status == HolderStatus.ASSIGNED:
            leases_repo.remove_holder(connection, job_id, worker_id)


def new_lease(lease_id: str, job_id: str, worker_id: str, kind: TaskKind, now: datetime) -> Lease:
    return Lease(
        lease_id=lease_id,
        job_id=job_id,
        worker_id=worker_id,
        task_kind=kind,
        acquired_at=now,
        expires_at=now + timedelta(seconds=LEASE_TTL_SECONDS),
    )
