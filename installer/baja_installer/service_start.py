"""`BajaWorker.exe --service …`: how NSSM starts the worker.

At boot, Windows may start this service before Tailscale has brought its
network adapter up. The worker binds its file server (8710) or receiver
(8720) to the Tailscale address, and binding to an address the PC does not
have yet fails; the worker would then run without its server. So, when
worker.json names a `tailnet_address`, wait until that address exists on
this PC before starting the worker. If it never appears, exit with
EXIT_TAILNET_NOT_READY and let NSSM start us again after its restart delay.

Before enrollment there is no address yet, and nothing to wait for.
"""

import errno
import socket
import time
from collections.abc import Callable
from pathlib import Path

from worker.config import load_config

# EX_TEMPFAIL from BSD sysexits.h: "try again later". NSSM restarts on any
# exit code; the fixed value makes this case easy to find in the logs.
EXIT_TAILNET_NOT_READY = 75
TAILNET_WAIT_SECONDS = 180.0
TAILNET_POLL_SECONDS = 3.0


def address_is_local(address: str) -> bool:
    """True if this PC has `address`: binding a socket to it succeeds.

    Port 0 lets the system pick a free port, so nothing is really taken.
    A MagicDNS name instead of an IP is resolved first; if it does not
    resolve yet, Tailscale is not up either.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind((address, 0))
    except OSError as error:
        # "in use" is unusual for port 0, but it means the address exists.
        return error.errno == errno.EADDRINUSE
    return True


def wait_for_tailnet_address(
    config_path: Path,
    *,
    timeout_seconds: float = TAILNET_WAIT_SECONDS,
    poll_seconds: float = TAILNET_POLL_SECONDS,
    is_local: Callable[[str], bool] = address_is_local,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    say: Callable[[str], None] = print,
) -> bool:
    """True when the worker can start now; False after `timeout_seconds`."""
    config = load_config(config_path)
    address = config.listen_address or config.tailnet_address
    if not address:
        return True
    deadline = clock() + timeout_seconds
    told = False
    while not is_local(address):
        if clock() >= deadline:
            say(f"tailnet address {address} did not appear in {timeout_seconds:.0f} s")
            return False
        if not told:
            say(f"waiting for the tailnet address {address} (Tailscale starting?)")
            told = True
        sleep(poll_seconds)
    return True
