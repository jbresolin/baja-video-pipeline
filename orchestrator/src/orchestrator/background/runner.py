"""Run the sweepers forever on asyncio, each on its own interval.

Each pass runs in a worker thread (asyncio.to_thread), because the sweepers
do blocking SQLite and file work. One failing pass is logged and the loop
carries on; a sweeper must never take the service down.
"""

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass

from orchestrator.background import sweepers
from orchestrator.context import OrchestratorContext
from orchestrator.domain.results_sync import sync_if_due
from orchestrator.domain.sessions import delete_idle_sessions

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PeriodicTask:
    name: str
    interval_s: float
    run_once: Callable[[OrchestratorContext], object]


PERIODIC_TASKS = [
    PeriodicTask("lease-expiry-sweeper", 5, sweepers.expire_leases_once),
    PeriodicTask("inbox-cleanup", 30, sweepers.clean_inbox_once),
    PeriodicTask("proxy-eviction", 60, sweepers.evict_proxies_once),
    PeriodicTask("outbox-cleanup", 60, sweepers.clean_outbox_once),
    # Provas and teams from the results API: every 6 h while an event is set
    # (checked every 10 min; it also retries a failed sync after 15 min).
    PeriodicTask("results-sync", 600, sync_if_due),
    # Expired sessions are already refused when used; this only tidies the table.
    PeriodicTask(
        "idle-session-cleanup",
        3600,
        lambda context: delete_idle_sessions(context, context.clock.now()),
    ),
]


async def run_periodically(task: PeriodicTask, context: OrchestratorContext) -> None:
    while True:
        try:
            await asyncio.to_thread(task.run_once, context)
        except Exception:  # noqa: BLE001 - log and keep the loop alive
            logger.exception("background task %s failed", task.name)
        await asyncio.sleep(task.interval_s)


def start_background_tasks(context: OrchestratorContext) -> list[asyncio.Task]:
    """Start every periodic task on the running event loop; cancel them on shutdown."""
    return [
        asyncio.create_task(run_periodically(task, context), name=task.name)
        for task in PERIODIC_TASKS
    ]
