"""Wait until this PC has its saved tailnet address, and say why while waiting.

The worker binds its file server (8710) or studio receiver (8720) to the
Tailscale address in worker.json (`listen_address` or `tailnet_address`),
so peers reach it only over the tailnet. Binding to an address the PC does
not have fails, so the daemon must not start before it exists.

This used to be done by `BajaWorker.exe --service` before the worker even
started: the status page never came up, and when the address was gone for
good (device removed from the tailnet, PC moved to another tailnet) the
service waited, exited and was restarted by NSSM forever, logging only
"waiting for the tailnet address … (Tailscale starting?)".

Now the status page is already up (worker.lifecycle) and this wait is a
visible state: the page says "Esta máquina não está conectada à rede
Baja." with what fixes it, the tray turns yellow, and when Tailscale runs
with another address the page says so explicitly instead of waiting
quietly.
"""

import logging
import threading
from collections.abc import Callable
from pathlib import Path

from worker.config import WorkerConfig
from worker.config_problems import require_usable_config
from worker.status import Phase, StatusBoard, TailnetProblem, TailnetProblemKind
from worker.tailscale import TailscaleStatus, address_is_local, read_tailscale_status

logger = logging.getLogger(__name__)

TAILNET_POLL_SECONDS = 3.0


def tailnet_problem_for(
    address: str,
    *,
    is_local: Callable[[str], bool] = address_is_local,
    read_status: Callable[[], TailscaleStatus | None] = read_tailscale_status,
) -> TailnetProblem | None:
    """None when this PC has `address`; else what Tailscale says instead."""
    if is_local(address):
        return None
    status = read_status()
    if status is None:
        return TailnetProblem(TailnetProblemKind.TAILSCALE_NOT_ANSWERING, address)
    if not status.is_running:
        return TailnetProblem(TailnetProblemKind.NOT_CONNECTED, address, status.ipv4)
    if status.ipv4 and status.ipv4 != address:
        return TailnetProblem(TailnetProblemKind.ADDRESS_NOT_ON_THIS_PC, address, status.ipv4)
    # Running with our address but not usable yet: the adapter is still
    # coming up. Same words as "not answering": wait a moment.
    return TailnetProblem(TailnetProblemKind.TAILSCALE_NOT_ANSWERING, address, status.ipv4)


def wait_for_tailnet(
    config_path: Path,
    status: StatusBoard,
    should_stop: Callable[[float], bool],
    *,
    check: Callable[[str], TailnetProblem | None] = tailnet_problem_for,
    poll_seconds: float = TAILNET_POLL_SECONDS,
) -> WorkerConfig | None:
    """The config once its tailnet address is on this PC; None when asked to stop.

    `should_stop(seconds)` waits up to that long and answers True when the
    worker is stopping or restarting (worker.lifecycle). worker.json is
    read again on every round: "Concluir conexão" or "Usar a rede atual"
    rewrite the address while we wait. A worker.json that stops parsing
    raises ConfigProblemError (the lifecycle then shows that instead).
    """
    last_kind = None
    while True:
        config = require_usable_config(config_path)
        address = config.listen_address or config.tailnet_address
        # No address saved: the daemon asks `tailscale ip -4` and binds to it,
        # or to every interface; nothing to wait for (design §17, worker/README).
        problem = check(address) if address else None
        if problem is None:
            status.set_tailnet_problem(None)
            if last_kind is not None:
                logger.info("tailnet address %s is on this PC now", address)
            return config
        status.set_phase(Phase.WAITING_FOR_TAILNET)
        status.set_tailnet_problem(problem)
        if problem.kind != last_kind:
            logger.warning(
                "not starting yet: tailnet address %s is not on this PC (%s; Tailscale has %s)",
                problem.saved_address,
                problem.kind,
                problem.current_address or "no address",
            )
            last_kind = problem.kind
        if should_stop(poll_seconds):
            return None


def waiter(shutdown: threading.Event, restart: threading.Event) -> Callable[[float], bool]:
    """should_stop for wait_for_tailnet: wait in short steps for either event."""

    def should_stop(seconds: float) -> bool:
        remaining = seconds
        while remaining > 0:
            step = min(0.5, remaining)
            if shutdown.wait(step) or restart.is_set():
                return True
            remaining -= step
        return shutdown.is_set() or restart.is_set()

    return should_stop
