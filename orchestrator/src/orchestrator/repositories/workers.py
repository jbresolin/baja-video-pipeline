"""SQL for workers, enrollment tokens and the rolling stats samples."""

import json
import sqlite3
from datetime import datetime

from orchestrator.db.database import from_db_time, to_db_time
from shared.models import (
    EnrollmentToken,
    MeasurementKind,
    Worker,
    WorkerCapabilities,
    WorkerRole,
    WorkerStats,
)

# Rolling stats average over this many newest samples of each kind.
STATS_WINDOW_SAMPLES = 10
BITS_PER_BYTE = 8
BYTES_PER_GB = 1_000_000_000


def insert_worker(
    connection: sqlite3.Connection,
    *,
    worker_id: str,
    name: str,
    role: WorkerRole,
    hostname: str | None,
    capabilities: WorkerCapabilities,
    bearer_token_sha256: str,
    storage_cap_bytes: int,
    now: datetime,
) -> None:
    connection.execute(
        """
        INSERT INTO workers (id, name, role, hostname, capabilities_json,
                             bearer_token_sha256, storage_cap_bytes, enrolled_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            worker_id,
            name,
            str(role),
            hostname,
            capabilities.model_dump_json(),
            bearer_token_sha256,
            storage_cap_bytes,
            to_db_time(now),
        ),
    )


def get_worker_row(connection: sqlite3.Connection, worker_id: str) -> sqlite3.Row | None:
    return connection.execute("SELECT * FROM workers WHERE id = ?", (worker_id,)).fetchone()


def find_worker_by_token_hash(
    connection: sqlite3.Connection, token_sha256: str
) -> sqlite3.Row | None:
    return connection.execute(
        "SELECT * FROM workers WHERE bearer_token_sha256 = ?", (token_sha256,)
    ).fetchone()


def list_worker_rows(connection: sqlite3.Connection) -> list[sqlite3.Row]:
    return connection.execute("SELECT * FROM workers ORDER BY enrolled_at, id").fetchall()


def record_heartbeat(
    connection: sqlite3.Connection,
    worker_id: str,
    now: datetime,
    *,
    free_bytes: int,
    storage_cap_bytes: int,
    tailnet_address: str | None,
    file_server_port: int | None,
    receiver_port: int | None,
) -> None:
    """Store what the heartbeat reported. Addresses left out keep their old value."""
    connection.execute(
        """
        UPDATE workers SET
            free_bytes = ?, storage_cap_bytes = ?, last_heartbeat = ?,
            tailnet_address = COALESCE(?, tailnet_address),
            file_server_port = COALESCE(?, file_server_port),
            receiver_port = COALESCE(?, receiver_port)
        WHERE id = ?
        """,
        (
            free_bytes,
            storage_cap_bytes,
            to_db_time(now),
            tailnet_address,
            file_server_port,
            receiver_port,
            worker_id,
        ),
    )


def worker_from_row(connection: sqlite3.Connection, row: sqlite3.Row) -> Worker:
    return Worker(
        id=row["id"],
        name=row["name"],
        role=WorkerRole(row["role"]),
        capabilities=WorkerCapabilities.model_validate(json.loads(row["capabilities_json"])),
        free_bytes=row["free_bytes"],
        storage_cap_bytes=row["storage_cap_bytes"],
        stats=rolling_stats(connection, row["id"]),
        tailnet_address=row["tailnet_address"],
        file_server_port=row["file_server_port"],
        receiver_port=row["receiver_port"],
        enrolled_at=from_db_time(row["enrolled_at"]),
        last_heartbeat=from_db_time(row["last_heartbeat"]),
    )


# --- Stats samples ---


def insert_stat_sample(
    connection: sqlite3.Connection,
    worker_id: str,
    kind: MeasurementKind,
    byte_count: int,
    seconds: float,
    now: datetime,
) -> None:
    connection.execute(
        "INSERT INTO worker_stat_samples (worker_id, kind, byte_count, seconds, recorded_at)"
        " VALUES (?, ?, ?, ?, ?)",
        (worker_id, str(kind), byte_count, seconds, to_db_time(now)),
    )


def _window_totals(
    connection: sqlite3.Connection, worker_id: str, kind: MeasurementKind
) -> tuple[int, float] | None:
    """(total bytes, total seconds) over the newest samples, or None without samples."""
    rows = connection.execute(
        "SELECT byte_count, seconds FROM worker_stat_samples WHERE worker_id = ? AND kind = ?"
        " ORDER BY id DESC LIMIT ?",
        (worker_id, str(kind), STATS_WINDOW_SAMPLES),
    ).fetchall()
    if not rows:
        return None
    return sum(row["byte_count"] for row in rows), sum(row["seconds"] for row in rows)


def rolling_stats(connection: sqlite3.Connection, worker_id: str) -> WorkerStats:
    """Averages weighted by size: total bytes over total seconds of the newest samples.

    Weighting by size means one tiny transfer cannot swing the average.
    """
    stats = WorkerStats()
    download = _window_totals(connection, worker_id, MeasurementKind.DOWNLOAD)
    if download:
        stats.download_mbps = download[0] * BITS_PER_BYTE / 1_000_000 / download[1]
    upload = _window_totals(connection, worker_id, MeasurementKind.UPLOAD)
    if upload:
        stats.upload_mbps = upload[0] * BITS_PER_BYTE / 1_000_000 / upload[1]
    process = _window_totals(connection, worker_id, MeasurementKind.PROCESS)
    if process and process[0] > 0:
        stats.process_s_per_gb = process[1] / (process[0] / BYTES_PER_GB)
    return stats


# --- Enrollment tokens ---


def insert_enrollment_token(connection: sqlite3.Connection, token: EnrollmentToken) -> None:
    connection.execute(
        "INSERT INTO enrollment_tokens (token_sha256, volunteer_name, role, created_at,"
        " expires_at) VALUES (?, ?, ?, ?, ?)",
        (
            token.token_sha256,
            token.volunteer_name,
            str(token.role),
            to_db_time(token.created_at),
            to_db_time(token.expires_at),
        ),
    )


def get_enrollment_token(
    connection: sqlite3.Connection, token_sha256: str
) -> EnrollmentToken | None:
    row = connection.execute(
        "SELECT * FROM enrollment_tokens WHERE token_sha256 = ?", (token_sha256,)
    ).fetchone()
    if row is None:
        return None
    return EnrollmentToken(
        token_sha256=row["token_sha256"],
        volunteer_name=row["volunteer_name"],
        role=WorkerRole(row["role"]),
        created_at=from_db_time(row["created_at"]),
        expires_at=from_db_time(row["expires_at"]),
        used_at=from_db_time(row["used_at"]),
        used_by_worker_id=row["used_by_worker_id"],
    )


def mark_enrollment_token_used(
    connection: sqlite3.Connection,
    token_sha256: str,
    worker_id: str,
    tailscale_key_id: str | None,
    now: datetime,
) -> bool:
    """Spend the token. False if somebody else spent it first (a race)."""
    cursor = connection.execute(
        "UPDATE enrollment_tokens SET used_at = ?, used_by_worker_id = ?, tailscale_key_id = ?"
        " WHERE token_sha256 = ? AND used_at IS NULL",
        (to_db_time(now), worker_id, tailscale_key_id, token_sha256),
    )
    return cursor.rowcount == 1
