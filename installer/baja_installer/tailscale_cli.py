"""Runs `tailscale` and reads its answers (infra/tailnet/worker_join.md).

Only first_run.py and leave_tailnet.py use this; the worker daemon itself
never runs Tailscale. `status --json` is not a stable format (Tailscale
says so), so parse_status reads every field defensively.
"""

import json
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from baja_installer.layout import WINDOWS_TAILSCALE_EXE

# The tags a Baja auth key puts on a device (infra/tailnet/acl.hujson).
BAJA_DEVICE_TAGS = frozenset({"tag:worker", "tag:studio"})
UP_TIMEOUT = "120s"
# Our own limit is longer than --timeout so tailscale gets to report it.
COMMAND_TIMEOUT_SECONDS = 180

CommandRunner = Callable[[list[str]], subprocess.CompletedProcess]


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        args, capture_output=True, text=True, timeout=COMMAND_TIMEOUT_SECONDS, check=False
    )


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


def up_arguments(key_path: Path, hostname: str, *, switching_tailnet: bool) -> list[str]:
    """Arguments after `tailscale` for joining with a key file (worker_join.md §5).

    `--auth-key=file:` keeps the key out of the process list. `--reset`
    makes `up` accept our flags even if the volunteer had set others before
    (otherwise it refuses and asks for every non-default flag).
    `--force-reauth` is needed only to leave another tailnet.
    """
    arguments = [
        "up",
        f"--auth-key=file:{key_path}",
        f"--hostname={hostname}",
        "--unattended",
        "--accept-routes=false",
        f"--timeout={UP_TIMEOUT}",
        "--reset",
    ]
    if switching_tailnet:
        arguments.append("--force-reauth")
    return arguments


def find_tailscale_executable() -> str:
    """`tailscale` on PATH, else the MSI's default location.

    PATH is checked first, but right after the MSI install the new PATH
    entry is not visible to already-running programs yet.
    """
    on_path = shutil.which("tailscale")
    if on_path:
        return on_path
    return str(WINDOWS_TAILSCALE_EXE)


class TailscaleCli:
    def __init__(self, executable: str, run: CommandRunner = _run) -> None:
        self.executable = executable
        self._run = run

    def status(self) -> TailscaleStatus | None:
        """None if tailscale is missing or its service is not answering yet.
        The exit code is ignored: `status` may exit non-zero while logged
        out or stopped and still print the JSON we need."""
        try:
            finished = self._run([self.executable, "status", "--json"])
        except (OSError, subprocess.TimeoutExpired):
            return None
        return parse_status(finished.stdout or "")

    def up(
        self, key_path: Path, hostname: str, *, switching_tailnet: bool
    ) -> subprocess.CompletedProcess:
        arguments = up_arguments(key_path, hostname, switching_tailnet=switching_tailnet)
        try:
            return self._run([self.executable, *arguments])
        except (OSError, subprocess.TimeoutExpired) as error:
            return subprocess.CompletedProcess([self.executable, *arguments], 1, "", str(error))

    def logout(self) -> subprocess.CompletedProcess:
        try:
            return self._run([self.executable, "logout"])
        except (OSError, subprocess.TimeoutExpired) as error:
            return subprocess.CompletedProcess([self.executable, "logout"], 1, "", str(error))
