"""What Tailscale says about this PC, read-only (`tailscale status --json`).

The worker never changes Tailscale (joining is BajaWorker.exe --first-run,
installer/baja_installer/first_run.py). It only looks, to explain why it
cannot start yet: Tailscale not answering, logged out, or on another
tailnet where the saved address is not ours (worker.tailnet_wait).

`status --json` is not a stable format (Tailscale says so), so
parse_status reads every field defensively. The installer's TailscaleCli
(join, logout) builds on the same parser.
"""

import errno
import json
import os
import shutil
import socket
import subprocess
from dataclasses import dataclass
from pathlib import Path

# The tags a Baja auth key puts on a device (infra/tailnet/acl.hujson).
BAJA_DEVICE_TAGS = frozenset({"tag:worker", "tag:studio"})
# Tailscale's MSI installs here by default (infra/tailnet/worker_join.md §1).
WINDOWS_TAILSCALE_EXE = Path(r"C:\Program Files\Tailscale\tailscale.exe")
STATUS_TIMEOUT_SECONDS = 20


@dataclass(frozen=True)
class TailscaleStatus:
    backend_state: str  # "Running", "NeedsLogin", "Stopped", … ("" if unknown)
    tags: tuple[str, ...]
    ipv4: str | None
    dns_name: str | None  # MagicDNS name without the trailing dot
    tailnet_suffix: str | None

    @property
    def is_running(self) -> bool:
        return self.backend_state == "Running"

    @property
    def on_baja_tailnet(self) -> bool:
        """Running with a Baja tag. A volunteer's own tailnet has no device
        tagged tag:worker or tag:studio by accident, so the tag is the test."""
        return self.is_running and bool(BAJA_DEVICE_TAGS.intersection(self.tags))


def parse_status(text: str) -> TailscaleStatus | None:
    """`tailscale status --json` → TailscaleStatus; None if it is not JSON."""
    try:
        data = json.loads(text)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    me = data.get("Self") if isinstance(data.get("Self"), dict) else {}
    tailnet = data.get("CurrentTailnet") if isinstance(data.get("CurrentTailnet"), dict) else {}
    addresses = me.get("TailscaleIPs") or []
    ipv4 = next((a for a in addresses if isinstance(a, str) and ":" not in a), None)
    dns_name = me.get("DNSName") or None
    return TailscaleStatus(
        backend_state=str(data.get("BackendState") or ""),
        tags=tuple(tag for tag in (me.get("Tags") or []) if isinstance(tag, str)),
        ipv4=ipv4,
        dns_name=dns_name.rstrip(".") if isinstance(dns_name, str) else None,
        tailnet_suffix=tailnet.get("MagicDNSSuffix") or None,
    )


def find_tailscale_executable() -> str:
    """`tailscale` on PATH, else the MSI's default location.

    PATH is checked first, but right after the MSI install the new PATH
    entry is not visible to already-running programs (or to the service,
    until the next reboot) yet.
    """
    on_path = shutil.which("tailscale")
    if on_path:
        return on_path
    return str(WINDOWS_TAILSCALE_EXE)


def read_tailscale_status(executable: str | None = None) -> TailscaleStatus | None:
    """None if tailscale is missing or its service is not answering yet.

    The exit code is ignored: `status` exits non-zero while logged out or
    stopped and still prints the JSON we need.
    """
    try:
        finished = subprocess.run(
            [executable or find_tailscale_executable(), "status", "--json"],
            capture_output=True,
            text=True,
            timeout=STATUS_TIMEOUT_SECONDS,
            check=False,
            # The service has no console; never flash a window on a desktop either.
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return parse_status(finished.stdout or "")


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
