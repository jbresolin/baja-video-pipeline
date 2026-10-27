"""Lease checks, renewal and expiry (docs/api.md §5).

A lease lives LEASE_TTL_SECONDS (45 s) after its last renewal. Expired
leases are ended by sweep_expired_leases(), which runs both in the
background (background/sweepers.py) and at the start of every worker-loop
request, so a request never trusts a lease that has already run out.
"""

import sqlite3
from datetime import datetime

from orchestrator.domain.transitions import drop_unconfirmed_holder, end_lease, move_job
from orchestrator.errors import lease_lost
from orchestrator.repositories import jobs as jobs_repo
from orchestrator.repositories import leases as leases_repo
from orchestrator.repositories.events import SYSTEM_ACTOR
from shared.models import JobState, Lease, TaskKind
from shared.protocol import LEASE_TTL_SECONDS


def sweep_expired_leases(connection: sqlite3.Connection, now: datetime) -> list[Lease]:
    """End every lease whose time is up and put its work back in the queue.

    * process: the job goes claimed → ready, so another holder can claim it.
    * download_raw: the unconfirmed holder slot is freed for another worker.
    * make_proxy / deliver: nothing else; the task is simply offered again.
    """
    expired = leases_repo.list_expired_leases(connection, now)
    for lease in expired:
        end_lease(
            connection,
            lease,
            now,
            actor=SYSTEM_ACTOR,
            reason="no renewal within the lease TTL",
            kind="lease_expired",
        )
        if lease.task_kind == TaskKind.DOWNLOAD_RAW:
            drop_unconfirmed_holder(connection, lease.job_id, lease.worker_id)
        if lease.task_kind == TaskKind.PROCESS:
            job_row = jobs_repo.get_job_row(connection, lease.job_id)
            if job_row is not None and job_row["state"] == JobState.CLAIMED:
                move_job(
                    connection,
                    job_row,
                    JobState.READY,
                    now,
                    actor=SYSTEM_ACTOR,
                    reason=f"process lease of worker {lease.worker_id} expired",
                )
    return expired


def require_active_lease(
    connection: sqlite3.Connection,
    lease_id: str,
    *,
    worker_id: str,
    job_id: str,
    kinds: set[TaskKind] | None = None,
    now: datetime,
) -> Lease:
    """The caller's live lease on this job, or a 409 lease_lost ApiError.

    "Not yours", "other job", "wrong task" and "expired" all answer
    lease_lost: in every case the worker must stop the task.
    """
    lease = leases_repo.get_lease(connection, lease_id)
    if (
        lease is None
        or lease.worker_id != worker_id
        or lease.job_id != job_id
        or (kinds is not None and lease.task_kind not in kinds)
        or lease.expires_at <= now
    ):
        raise lease_lost(lease_id)
    return lease


def renew_lease(connection: sqlite3.Connection, lease: Lease, now: datetime) -> Lease:
    expires_at = leases_repo.extend_lease(connection, lease.lease_id, now, LEASE_TTL_SECONDS)
    return lease.model_copy(update={"expires_at": expires_at})
