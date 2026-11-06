"""Runs `tailscale` and reads its answers (infra/tailnet/worker_join.md).

Only first_run.py and leave_tailnet.py use this; the worker daemon itself
never runs Tailscale. `status --json` is not a stable format (Tailscale
says so), so parse_status reads every field defensively.
"""

import subprocess
from collections.abc import Callable
from pathlib import Path

# The parser and the status type live in the worker, which reads Tailscale's
# status too (worker.tailnet_wait); re-exported here for the installer code.
from worker.tailscale import (
    BAJA_DEVICE_TAGS,
    TailscaleStatus,
    find_tailscale_executable,
    parse_status,
)

__all__ = [
    "BAJA_DEVICE_TAGS",
    "TailscaleCli",
    "TailscaleStatus",
    "find_tailscale_executable",
    "parse_status",
    "up_arguments",
]

UP_TIMEOUT = "120s"
# Our own limit is longer than --timeout so tailscale gets to report it.
COMMAND_TIMEOUT_SECONDS = 180

CommandRunner = Callable[[list[str]], subprocess.CompletedProcess]


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        args, capture_output=True, text=True, timeout=COMMAND_TIMEOUT_SECONDS, check=False
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
