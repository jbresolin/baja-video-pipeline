"""When the VPS may drop its inbox copy of a raw (design §4, §10).

The inbox is transit only: once two holders confirmed the raw, the VPS
copy goes. Used by holder-confirm (right away) and the background inbox
cleanup (catches anything missed, and drops the copy of a discarded job
once its discard grace period is over: domain/discard.py).
"""

import logging
import sqlite3
from datetime import datetime

from orchestrator.repositories import events as events_repo
from orchestrator.repositories import jobs as jobs_repo
from orchestrator.repositories import leases as leases_repo
from orchestrator.storage import VpsLayout, delete_file_and_partials
from shared.models import HolderStatus, JobState, TaskKind
from shared.protocol import REQUIRED_HOLDER_COUNT

logger = logging.getLogger(__name__)


def confirmed_holder_count(connection: sqlite3.Connection, job_id: str) -> int:
    return sum(
        1
        for holder in leases_repo.list_holders(connection, job_id)
        if holder.status == HolderStatus.CONFIRMED
    )


def inbox_copy_is_redundant(connection: sqlite3.Connection, job_row: sqlite3.Row) -> bool:
    """Two confirmed holders and no inbox download running.

    A discarded job's copy is not "redundant": it is kept until the discard
    grace period ends, so a restore finds it (background/sweepers.py).
    """
    if job_row["state"] == JobState.DISCARDED:
        return False
    if confirmed_holder_count(connection, job_row["id"]) < REQUIRED_HOLDER_COUNT:
        return False
    # Never pull the file from under a running inbox download.
    return not leases_repo.list_leases_for_job(connection, job_row["id"], TaskKind.DOWNLOAD_RAW)


def drop_inbox_copy(
    connection: sqlite3.Connection,
    layout: VpsLayout,
    job_row: sqlite3.Row,
    now: datetime,
    *,
    actor: str,
    reason: str,
) -> bool:
    """Delete the inbox file and set raw_on_vps=false. True if it was deleted now."""
    if not job_row["raw_on_vps"]:
        return False
    path = layout.inbox_path(job_row["id"], job_row["raw_extension"])
    try:
        delete_file_and_partials(path)
    except OSError as error:
        logger.warning("could not delete %s: %s", path, error)
        return False
    jobs_repo.update_job(connection, job_row["id"], now, {"raw_on_vps": 0})
    events_repo.record_event(
        connection, now, actor=actor, kind="inbox_copy_deleted", job_id=job_row["id"], reason=reason
    )
    return True
