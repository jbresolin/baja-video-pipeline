"""Adopting a new role from the heartbeat answer (LIVESTACK-42 item 8).

An admin can make another worker the master on Admin → Workers ("Tornar
master"); the old master becomes a plain worker. Each heartbeat sends the
role this worker runs as and gets back the role the orchestrator has for
it. When they differ, the worker writes the new role into worker.json and
restarts, so every part (task kinds, storage cap rule, archive) starts in
the new role. No one edits the JSON by hand.

"Restart" means: stop cleanly, like a CTRL-C. Installed as a Windows
service, NSSM starts the worker again 10 s later (installer: AppExit
Default Restart); running by hand, start it again.

Only master ↔ worker: a studio has another tailnet tag and must enroll
again, so a studio role in either direction is ignored (and logged).
"""

import logging
from collections.abc import Callable
from pathlib import Path

from shared.models import WorkerRole
from worker.config import load_config, save_config

logger = logging.getLogger(__name__)

SWITCHABLE_ROLES = frozenset({WorkerRole.MASTER, WorkerRole.WORKER})


def adopt_role_from_heartbeat(
    config_path: Path,
    running_role: WorkerRole,
    answered_role: WorkerRole | None,
    restart: Callable[[], None],
) -> bool:
    """Save `answered_role` and call `restart` when it is a switch we accept.

    Returns True when it did. Nothing happens when the answer has no role
    (an older orchestrator), the same role, or a studio on either side.
    """
    if answered_role is None or answered_role == running_role:
        return False
    if running_role not in SWITCHABLE_ROLES or answered_role not in SWITCHABLE_ROLES:
        logger.warning(
            "the orchestrator says role %s but this PC runs as %s; only master and worker"
            " switch by themselves (a studio enrolls again)",
            answered_role,
            running_role,
        )
        return False
    # Read-modify-write so nothing else in worker.json is lost.
    config = load_config(config_path)
    save_config(config_path, config.model_copy(update={"role": answered_role}))
    logger.info("an admin made this PC %s (was %s); restarting", answered_role, running_role)
    restart()
    return True
