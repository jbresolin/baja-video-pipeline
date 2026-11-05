"""GET /fleet (workers, stats, disk, leases, job counts) and GET /workers (peer list)."""

from orchestrator.context import OrchestratorContext
from orchestrator.db.database import from_db_time
from orchestrator.domain.clip_archive import archive_progress, worker_clip_counts
from orchestrator.domain.fleet_snapshot import accepts_new_raws, is_online
from orchestrator.repositories import jobs as jobs_repo
from orchestrator.repositories import leases as leases_repo
from orchestrator.repositories import workers as workers_repo
from shared.models import FleetResponse, FleetWorker, Peer, PeersResponse, ServerDisk, WorkerRole


def build_fleet(context: OrchestratorContext) -> FleetResponse:
    now = context.clock.now()
    with context.database.transaction() as connection:
        fleet_workers = []
        for row in workers_repo.list_worker_rows(connection):
            held_count, held_bytes = leases_repo.held_bytes_for_worker(connection, row["id"])
            clips_held, clips_archived = worker_clip_counts(connection, row["id"])
            fleet_workers.append(
                FleetWorker(
                    worker=workers_repo.worker_from_row(connection, row),
                    online=is_online(row, now),
                    leases=leases_repo.list_leases_for_worker(connection, row["id"]),
                    held_job_count=held_count,
                    held_bytes=held_bytes,
                    accepting_new_raws=accepts_new_raws(row),
                    clips_held=clips_held,
                    clips_archived=clips_archived,
                    paused=bool(row["paused"]),
                )
            )
        jobs_by_state = jobs_repo.count_jobs_by_state(connection)
        progress = archive_progress(connection)
    return FleetResponse(
        workers=fleet_workers,
        server_disk=server_disk(context),
        jobs_by_state=jobs_by_state,
        clip_archive=progress,
    )


def server_disk(context: OrchestratorContext) -> ServerDisk:
    """VPS disk use and caps (fleet page and admin Settings page)."""
    layout = context.layout
    return ServerDisk(
        inbox_bytes=layout.inbox_bytes(),
        inbox_hold_bytes=context.admin_settings.inbox_hold_bytes,
        inbox_cap_bytes=context.settings.inbox_cap_bytes,
        proxy_bytes=layout.proxy_bytes(),
        outbox_bytes=layout.outbox_bytes(),
        free_bytes=layout.free_bytes(),
    )


def list_peers(context: OrchestratorContext) -> PeersResponse:
    now = context.clock.now()
    with context.database.transaction() as connection:
        rows = workers_repo.list_worker_rows(connection)
    return PeersResponse(
        workers=[
            Peer(
                worker_id=row["id"],
                name=row["name"],
                role=WorkerRole(row["role"]),
                tailnet_address=row["tailnet_address"],
                file_server_port=row["file_server_port"],
                receiver_port=row["receiver_port"],
                online=is_online(row, now),
                last_heartbeat=from_db_time(row["last_heartbeat"]),
            )
            for row in rows
        ]
    )
