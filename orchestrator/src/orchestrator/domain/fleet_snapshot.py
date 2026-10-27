"""A moment-in-time view of the workers, used to place tasks (design §5).

"Online" means a heartbeat within the lease TTL (45 s = three missed beats).
"""

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta

from orchestrator.db.database import from_db_time
from orchestrator.repositories import leases as leases_repo
from orchestrator.repositories import workers as workers_repo
from shared.models import TaskKind, WorkerRole
from shared.protocol import DEFAULT_WORKER_FILE_SERVER_PORT, LEASE_TTL_SECONDS

# A worker with less free space than this under its cap takes no new raws
# (design §9). Two maximum-size WhatsApp documents (~2 GB each).
NEAR_CAP_HEADROOM_BYTES = 4_000_000_000


def is_online(worker_row: sqlite3.Row, now: datetime) -> bool:
    last_heartbeat = from_db_time(worker_row["last_heartbeat"])
    return last_heartbeat is not None and now - last_heartbeat < timedelta(
        seconds=LEASE_TTL_SECONDS
    )


def accepts_new_raws(worker_row: sqlite3.Row) -> bool:
    """False for the studio and for workers near their storage cap."""
    return (
        worker_row["role"] != WorkerRole.STUDIO
        and worker_row["free_bytes"] >= NEAR_CAP_HEADROOM_BYTES
    )


def peer_raw_url(worker_row: sqlite3.Row, job_id: str) -> str | None:
    """http://<tailnet address>:8710/raw/<job_id>, or None if the worker has no address yet."""
    address = worker_row["tailnet_address"]
    if not address:
        return None
    port = worker_row["file_server_port"] or DEFAULT_WORKER_FILE_SERVER_PORT
    return f"http://{address}:{port}/raw/{job_id}"


@dataclass
class FleetSnapshot:
    now: datetime
    workers_by_id: dict[str, sqlite3.Row]
    # Workers busy downloading right now (they hold a download_raw lease).
    downloading_worker_ids: set[str]
    # Download speed per worker (Mbps), None when not measured yet.
    download_mbps_by_worker: dict[str, float | None]

    @classmethod
    def take(cls, connection: sqlite3.Connection, now: datetime) -> "FleetSnapshot":
        rows = workers_repo.list_worker_rows(connection)
        downloading = {
            lease.worker_id
            for row in rows
            for lease in leases_repo.list_leases_for_worker(connection, row["id"])
            if lease.task_kind == TaskKind.DOWNLOAD_RAW
        }
        speeds = {
            row["id"]: workers_repo.rolling_stats(connection, row["id"]).download_mbps
            for row in rows
        }
        return cls(now, {row["id"]: row for row in rows}, downloading, speeds)

    def is_online(self, worker_id: str) -> bool:
        row = self.workers_by_id.get(worker_id)
        return row is not None and is_online(row, self.now)

    def is_master(self, worker_id: str) -> bool:
        row = self.workers_by_id.get(worker_id)
        return row is not None and row["role"] == WorkerRole.MASTER

    def any_master_online(self) -> bool:
        return any(
            row["role"] == WorkerRole.MASTER and is_online(row, self.now)
            for row in self.workers_by_id.values()
        )
