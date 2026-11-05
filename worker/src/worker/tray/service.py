"""The BajaWorker Windows service, seen from the tray (runs as the logged-in user).

`sc.exe` does the work: `sc query` for the state, `sc start` / `sc stop`
for "Ligar" / "Desligar". The installer grants the built-in Users group
the right to query, start and stop this one service (service security
descriptor via `sc sdset`, installer/README.md "Direitos do serviço"), so
no UAC prompt is needed. If Windows still says "access denied" (an install
from before that step), the tray asks again through a UAC prompt.

The parsing and the decisions are plain functions so the Linux test suite
covers them; `run_sc` is the only part that touches Windows.
"""

import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass

from worker.tray.state import ServiceState

SERVICE_NAME = "BajaWorker"
SC_TIMEOUT_SECONDS = 30
# sc.exe exit codes (Win32 error numbers) we tell apart.
ERROR_ACCESS_DENIED = 5
ERROR_SERVICE_ALREADY_RUNNING = 1056
ERROR_SERVICE_NOT_ACTIVE = 1062
ERROR_SERVICE_DOES_NOT_EXIST = 1060

# "        STATE              : 4  RUNNING". We read the number, which means
# the same on every Windows, rather than the word after it.
_STATE_LINE = re.compile(r"^\s*STATE\s*:\s*(\d+)", re.MULTILINE)
_STATE_BY_NUMBER = {
    1: ServiceState.STOPPED,
    2: ServiceState.STARTING,
    3: ServiceState.STOPPING,
    4: ServiceState.RUNNING,
    5: ServiceState.STARTING,  # continue pending
    6: ServiceState.STOPPING,  # pause pending
    7: ServiceState.STOPPED,  # paused (NSSM services are never paused, but be safe)
}


@dataclass(frozen=True)
class ScResult:
    returncode: int
    output: str


def parse_query_output(result: ScResult) -> ServiceState:
    """`sc query BajaWorker` → our ServiceState."""
    if result.returncode != 0:
        return ServiceState.UNKNOWN
    match = _STATE_LINE.search(result.output)
    if match is None:
        return ServiceState.UNKNOWN
    return _STATE_BY_NUMBER.get(int(match.group(1)), ServiceState.UNKNOWN)


def needs_elevation(result: ScResult) -> bool:
    """`sc start/stop` refused for lack of rights: retry behind a UAC prompt."""
    return result.returncode == ERROR_ACCESS_DENIED


def control_succeeded(result: ScResult, command: str) -> bool:
    """True when the service is (or is getting) where we asked."""
    if result.returncode == 0:
        return True
    if command == "start":
        return result.returncode == ERROR_SERVICE_ALREADY_RUNNING
    return result.returncode == ERROR_SERVICE_NOT_ACTIVE


def run_sc(arguments: list[str]) -> ScResult:
    """Run sc.exe without a console window."""
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        finished = subprocess.run(
            ["sc.exe", *arguments],
            capture_output=True,
            text=True,
            timeout=SC_TIMEOUT_SECONDS,
            check=False,
            creationflags=flags,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return ScResult(-1, str(error))
    return ScResult(finished.returncode, finished.stdout + finished.stderr)


class ServiceController:
    """Query / start / stop the service; `run` and `run_elevated` are injectable."""

    def __init__(
        self,
        run: Callable[[list[str]], ScResult] = run_sc,
        run_elevated: Callable[[list[str]], bool] | None = None,
    ) -> None:
        self._run = run
        self._run_elevated = run_elevated

    def state(self) -> ServiceState:
        return parse_query_output(self._run(["query", SERVICE_NAME]))

    def start(self) -> bool:
        return self._control("start")

    def stop(self) -> bool:
        return self._control("stop")

    def _control(self, command: str) -> bool:
        result = self._run([command, SERVICE_NAME])
        if control_succeeded(result, command):
            return True
        if needs_elevation(result) and self._run_elevated is not None:
            return self._run_elevated([command, SERVICE_NAME])
        return False
