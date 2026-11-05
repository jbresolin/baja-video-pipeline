"""Periodic housekeeping. Each function does one pass and returns what it did.

They take the context (and so its Clock), never sleep and never loop, so a
test can call one directly after moving a ManualClock forward. The asyncio
loop that calls them every few seconds is in background/runner.py.
"""

import logging
from dataclasses import dataclass, field
from datetime import timedelta

from orchestrator.context import OrchestratorContext
from orchestrator.domain.delivery import delete_outbox_copy_if_unneeded, finish_delivery
from orchestrator.domain.discard import discard_grace_is_over
from orchestrator.domain.fleet_snapshot import FleetSnapshot
from orchestrator.domain.leases import sweep_expired_leases
from orchestrator.domain.raw_copies import drop_inbox_copy, inbox_copy_is_redundant
from orchestrator.domain.review_queue import NO_HOLDER_ONLINE
from orchestrator.repositories import events as events_repo
from orchestrator.repositories import jobs as jobs_repo
from orchestrator.repositories import leases as leases_repo
from orchestrator.repositories.events import SYSTEM_ACTOR
from orchestrator.storage import delete_file_and_partials
from shared.models import HolderStatus, JobState

logger = logging.getLogger(__name__)

# Half-written outbox uploads untouched for this long are abandoned.
ABANDONED_UPLOAD_AGE = timedelta(hours=24)


def expire_leases_once(context: OrchestratorContext) -> list[str]:
    """End expired leases (process jobs go back to ready); refresh waiting reasons.

    Returns the ids of the leases that expired.
    """
    with context.database.transaction() as connection:
        now = context.clock.now()
        expired = sweep_expired_leases(connection, now)
        _refresh_waiting_reasons(connection, now)
    return [lease.lease_id for lease in expired]


def _refresh_waiting_reasons(connection, now) -> None:
    """Say "waiting for a holder" on ready jobs (and requested proxies) with no holder online.

    Only ever sets or clears its own sentence, never a failure message.
    """
    snapshot = FleetSnapshot.take(connection, now)
    rows = jobs_repo.list_job_rows_in_states(connection, [JobState.READY])
    rows += [
        row
        for row in jobs_repo.list_job_rows_needing_proxy(connection)
        if row["proxy_requested"] and row["state"] != JobState.READY
    ]
    for row in rows:
        holder_online = any(
            holder.status == HolderStatus.CONFIRMED and snapshot.is_online(holder.worker_id)
            for holder in leases_repo.list_holders(connection, row["id"])
        )
        wanted = None if holder_online else NO_HOLDER_ONLINE
        current = row["waiting_reason"]
        if current == wanted or (current not in (None, NO_HOLDER_ONLINE)):
            continue
        jobs_repo.update_job(connection, row["id"], now, {"waiting_reason": wanted})


def clean_inbox_once(context: OrchestratorContext) -> list[str]:
    """Delete inbox raws held by two confirmed holders, or of jobs discarded past the grace."""
    deleted = []
    with context.database.transaction() as connection:
        now = context.clock.now()
        for row in jobs_repo.list_job_rows_with_raw_on_vps(connection):
            if discard_grace_is_over(row, now):
                reason = "discard grace period over"
            elif inbox_copy_is_redundant(connection, row):
                reason = "inbox cleanup"
            else:
                continue
            if drop_inbox_copy(
                connection, context.layout, row, now, actor=SYSTEM_ACTOR, reason=reason
            ):
                deleted.append(row["id"])
    return deleted


def evict_proxies_once(context: OrchestratorContext) -> list[str]:
    """Below BVP_PROXY_EVICT_MIN_FREE_GB free, delete proxies of finished jobs, oldest first.

    Any holder can render a proxy again on request (regenerate-proxy).
    """
    evicted = []
    minimum_free = context.admin_settings.proxy_evict_min_free_bytes
    if context.layout.free_bytes() >= minimum_free:
        return evicted
    with context.database.transaction() as connection:
        now = context.clock.now()
        for row in jobs_repo.list_finished_job_rows_with_proxy(connection):
            if context.layout.free_bytes() >= minimum_free:
                break
            delete_file_and_partials(context.layout.proxy_path(row["id"]))
            jobs_repo.update_job(connection, row["id"], now, {"proxy_available": 0})
            events_repo.record_event(
                connection,
                now,
                actor=SYSTEM_ACTOR,
                kind="proxy_evicted",
                job_id=row["id"],
                reason="VPS disk below threshold",
            )
            evicted.append(row["id"])
    return evicted


@dataclass
class OutboxCleanupResult:
    finished_job_ids: list[str] = field(default_factory=list)
    deleted_files: list[str] = field(default_factory=list)


def clean_outbox_once(context: OrchestratorContext) -> OutboxCleanupResult:
    """Finish delivered jobs whose copy is still here; delete files nobody needs.

    An outbox file is needed only while its job is `uploaded` (waiting for
    the studio to pull it) or `claimed` (just uploaded). Abandoned
    half-uploads go after 24 hours.
    """
    result = OutboxCleanupResult()
    outbox = context.layout.outbox_dir
    if not outbox.exists():
        return result
    with context.database.transaction() as connection:
        now = context.clock.now()
        for row in jobs_repo.list_job_rows_in_states(connection, [JobState.DELIVERED]):
            finish_delivery(context, connection, row["id"], now, SYSTEM_ACTOR)
            result.finished_job_ids.append(row["id"])
        for path in sorted(outbox.iterdir()):
            if path.name.startswith("."):
                age = now.timestamp() - path.stat().st_mtime
                if age > ABANDONED_UPLOAD_AGE.total_seconds():
                    path.unlink(missing_ok=True)
                    result.deleted_files.append(path.name)
                continue
            job_row = jobs_repo.get_job_row(connection, path.stem)
            if job_row is None:
                delete_file_and_partials(path)
                result.deleted_files.append(path.name)
            # claimed (the move to uploaded may follow in the next
            # transaction), uploaded (the studio has not pulled it), or
            # delivered but not yet archived by the master: keep it.
            elif delete_outbox_copy_if_unneeded(context, connection, job_row, now):
                result.deleted_files.append(path.name)
    return result
