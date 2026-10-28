"""Heartbeat, /next offers, claims and lease renewal (docs/api.md §5)."""

import sqlite3

from orchestrator.context import OrchestratorContext
from orchestrator.domain.fleet_snapshot import FleetSnapshot, accepts_new_raws
from orchestrator.domain.job_view import require_job_row
from orchestrator.domain.leases import renew_lease, require_active_lease, sweep_expired_leases
from orchestrator.domain.task_rules import evaluate_task
from orchestrator.domain.transitions import move_job, new_lease
from orchestrator.errors import ApiError
from orchestrator.repositories import events as events_repo
from orchestrator.repositories import jobs as jobs_repo
from orchestrator.repositories import leases as leases_repo
from orchestrator.repositories import workers as workers_repo
from orchestrator.security import new_id
from shared.models import (
    ClaimResponse,
    HeartbeatRequest,
    HeartbeatResponse,
    JobState,
    LeaseHeartbeatStatus,
    LeaseRenewResponse,
    NextTaskRequest,
    NextTaskResponse,
    TaskKind,
    WorkerRole,
)
from shared.protocol import (
    EMPTY_QUEUE_RETRY_SECONDS,
    HEARTBEAT_INTERVAL_SECONDS,
    REQUIRED_HOLDER_COUNT,
)

# Order in which /next looks at the kinds a worker accepts. Proxies first:
# they unblock the human reviewers, who are the slowest part of the line.
KIND_PRIORITY = [TaskKind.MAKE_PROXY, TaskKind.PROCESS, TaskKind.DOWNLOAD_RAW, TaskKind.DELIVER]
# After all of that, the master backfills raws of finished jobs it missed
# (design §17): the lowest priority there is.


def process_heartbeat(
    context: OrchestratorContext, worker_row: sqlite3.Row, request: HeartbeatRequest
) -> HeartbeatResponse:
    worker_id = worker_row["id"]
    with context.database.transaction() as connection:
        now = context.clock.now()
        sweep_expired_leases(connection, now)
        workers_repo.record_heartbeat(
            connection,
            worker_id,
            now,
            free_bytes=request.free_bytes,
            storage_cap_bytes=request.storage_cap_bytes,
            tailnet_address=request.tailnet_address,
            file_server_port=request.file_server_port,
            receiver_port=request.receiver_port,
        )
        for measurement in request.measurements:
            workers_repo.insert_stat_sample(
                connection,
                worker_id,
                measurement.kind,
                measurement.byte_count,
                measurement.seconds,
                now,
            )

        lease_status = {}
        for lease_id in request.current_lease_ids:
            lease = leases_repo.get_lease(connection, lease_id)
            if lease is not None and lease.worker_id == worker_id and lease.expires_at > now:
                renew_lease(connection, lease, now)
                lease_status[lease_id] = LeaseHeartbeatStatus.RENEWED
            else:
                lease_status[lease_id] = LeaseHeartbeatStatus.LOST

        if worker_row["role"] != WorkerRole.STUDIO:
            _forget_raws_the_worker_no_longer_has(connection, worker_id, request, now)
        deletable = _deletable_job_ids(connection, request.held_job_ids)
        refreshed_row = workers_repo.get_worker_row(connection, worker_id)

    return HeartbeatResponse(
        lease_status=lease_status,
        heartbeat_interval_s=HEARTBEAT_INTERVAL_SECONDS,
        accepting_new_raws=accepts_new_raws(refreshed_row),
        deletable_job_ids=deletable,
    )


def _forget_raws_the_worker_no_longer_has(
    connection: sqlite3.Connection, worker_id: str, request: HeartbeatRequest, now
) -> None:
    """Reconcile holders: a confirmed raw missing from held_job_ids is gone from that disk."""
    held = set(request.held_job_ids)
    for job_id in leases_repo.list_confirmed_job_ids_for_worker(connection, worker_id):
        if job_id in held:
            continue
        leases_repo.remove_holder(connection, job_id, worker_id)
        events_repo.record_event(
            connection,
            now,
            actor=events_repo.worker_actor(worker_id),
            kind="holder_lost",
            job_id=job_id,
            worker_id=worker_id,
            reason="raw no longer listed in the worker's held_job_ids",
        )


def _deletable_job_ids(connection: sqlite3.Connection, held_job_ids: list[str]) -> list[str]:
    """Held raws of discarded jobs (design §4). Unknown ids are never listed, to be safe."""
    deletable = []
    for job_id in held_job_ids:
        row = jobs_repo.get_job_row(connection, job_id)
        if row is not None and row["state"] == JobState.DISCARDED:
            deletable.append(job_id)
    return deletable


def _candidate_rows(connection: sqlite3.Connection, worker_row: sqlite3.Row, kind: TaskKind):
    worker_id = worker_row["id"]
    if kind == TaskKind.DOWNLOAD_RAW:
        # A non-master never joins a job that already has master + 2 others.
        is_master = worker_row["role"] == WorkerRole.MASTER
        limit = None if is_master else REQUIRED_HOLDER_COUNT + 1
        return jobs_repo.list_download_candidates(connection, worker_id, limit)
    if kind == TaskKind.MAKE_PROXY:
        return [
            row
            for row in jobs_repo.list_job_rows_needing_proxy(connection)
            if any(
                h.worker_id == worker_id and h.status == "confirmed"
                for h in leases_repo.list_holders(connection, row["id"])
            )
        ]
    if kind == TaskKind.PROCESS:
        return jobs_repo.list_jobs_held_by(connection, worker_id, [JobState.READY])
    return jobs_repo.list_outbox_deliver_candidates(connection)


def offer_next_task(
    context: OrchestratorContext, worker_row: sqlite3.Row, request: NextTaskRequest
) -> NextTaskResponse:
    """The first task this worker may do among the kinds it accepts; an offer, not a lease."""
    with context.database.transaction() as connection:
        now = context.clock.now()
        sweep_expired_leases(connection, now)
        snapshot = FleetSnapshot.take(connection, now)
        accepted = set(request.accept_kinds)
        for kind in KIND_PRIORITY:
            if kind not in accepted:
                continue
            # One task per stage (design §5): a worker that still holds a
            # lease of this kind is told nothing new for it.
            if any(
                lease.task_kind == kind
                for lease in leases_repo.list_leases_for_worker(connection, worker_row["id"])
            ):
                continue
            for job_row in _candidate_rows(connection, worker_row, kind):
                decision = evaluate_task(
                    connection, context.layout, snapshot, job_row, worker_row, kind
                )
                if decision.task is not None:
                    context.task_counters.count_offer(kind)
                    return NextTaskResponse(task=decision.task, retry_after_s=0)
        backfill = _master_backfill_offer(context, connection, snapshot, worker_row, accepted)
        if backfill is not None:
            context.task_counters.count_offer(TaskKind.DOWNLOAD_RAW)
            return NextTaskResponse(task=backfill, retry_after_s=0)
    return NextTaskResponse(task=None, retry_after_s=EMPTY_QUEUE_RETRY_SECONDS)


def _master_backfill_offer(
    context: OrchestratorContext,
    connection: sqlite3.Connection,
    snapshot: FleetSnapshot,
    worker_row: sqlite3.Row,
    accepted_kinds: set[TaskKind],
):
    """A download_raw of a done or failed job the master does not hold yet, or None.

    Only asked when nothing live was found. Same one-download-at-a-time rule
    as live downloads; evaluate_task picks the sources (inbox if it is still
    there, else online confirmed holders on :8710).
    """
    if worker_row["role"] != WorkerRole.MASTER or TaskKind.DOWNLOAD_RAW not in accepted_kinds:
        return None
    if any(
        lease.task_kind == TaskKind.DOWNLOAD_RAW
        for lease in leases_repo.list_leases_for_worker(connection, worker_row["id"])
    ):
        return None
    for job_row in jobs_repo.list_backfill_candidates(connection, worker_row["id"]):
        decision = evaluate_task(
            connection, context.layout, snapshot, job_row, worker_row, TaskKind.DOWNLOAD_RAW
        )
        if decision.task is not None:
            return decision.task
    return None


def claim_task(
    context: OrchestratorContext, worker_row: sqlite3.Row, job_id: str, kind: TaskKind
) -> ClaimResponse:
    worker_id = worker_row["id"]
    with context.database.transaction() as connection:
        now = context.clock.now()
        sweep_expired_leases(connection, now)
        job_row = require_job_row(connection, job_id)
        snapshot = FleetSnapshot.take(connection, now)
        decision = evaluate_task(connection, context.layout, snapshot, job_row, worker_row, kind)
        if decision.task is None:
            raise ApiError(409, decision.refusal, decision.reason)

        existing = [
            lease
            for lease in leases_repo.list_leases_for_job(connection, job_id, kind)
            if lease.worker_id == worker_id
        ]
        if existing:
            # Claiming again (e.g. the first answer was lost) renews the same lease.
            return ClaimResponse(
                lease=renew_lease(connection, existing[0], now), task=decision.task
            )

        lease = new_lease(new_id(), job_id, worker_id, kind, now)
        leases_repo.insert_lease(connection, lease)
        context.task_counters.count_claim(kind)
        actor = events_repo.worker_actor(worker_id)
        events_repo.record_event(
            connection,
            now,
            actor=actor,
            kind="lease_acquired",
            job_id=job_id,
            worker_id=worker_id,
            reason=f"{kind} lease {lease.lease_id}",
        )
        if kind == TaskKind.DOWNLOAD_RAW:
            leases_repo.add_assigned_holder(connection, job_id, worker_id, now)
        if kind == TaskKind.PROCESS:
            move_job(
                connection,
                job_row,
                JobState.CLAIMED,
                now,
                actor=actor,
                reason="holder claimed processing",
                changes={"waiting_reason": None},
            )
    return ClaimResponse(lease=lease, task=decision.task)


def renew_one_lease(
    context: OrchestratorContext, worker_row: sqlite3.Row, job_id: str, lease_id: str
) -> LeaseRenewResponse:
    with context.database.transaction() as connection:
        now = context.clock.now()
        sweep_expired_leases(connection, now)
        lease = require_active_lease(
            connection, lease_id, worker_id=worker_row["id"], job_id=job_id, now=now
        )
        return LeaseRenewResponse(lease=renew_lease(connection, lease, now))
