"""`BajaWorker.exe --grant-service-rights`: let ordinary users start and stop BajaWorker.

LIVESTACK-38 item 9. The tray icon runs as the logged-in volunteer, never
as administrator, and its "Desligar o worker" / "Ligar o worker" call
`sc stop` / `sc start`. By default Windows only lets administrators do
that, which would mean a UAC prompt on every click. So the installer adds
one entry to this one service's security descriptor:

    (A;;CCLCSWRPWPLORC;;;BU)

read as: Allow (A) the built-in Users group (BU) to query the
configuration (CC) and status (LC), list dependents (SW), start (RP),
stop (WP), interrogate (LO) and read the descriptor (RC). Nothing else:
users still cannot change, pause or delete the service, and no other
service is touched.

How: read the current descriptor with `sc sdshow BajaWorker`, insert the
entry into its DACL (the part before "S:"), write it back with
`sc sdset BajaWorker <sddl>`. Running it twice changes nothing. The
pure string function is tested on Linux; the Windows CI job checks that a
real non-admin user can stop and start the service.
"""

import subprocess
from collections.abc import Callable
from dataclasses import dataclass

from baja_installer.layout import SERVICE_NAME

USERS_START_STOP_ACE = "(A;;CCLCSWRPWPLORC;;;BU)"
SC_TIMEOUT_SECONDS = 30
EXIT_GRANTED = 0
EXIT_FAILED = 1


class SddlError(ValueError):
    pass


def add_users_start_stop(sddl: str) -> str:
    """The descriptor with USERS_START_STOP_ACE in its DACL (unchanged if there)."""
    sddl = sddl.strip()
    if not sddl.startswith("D:"):
        raise SddlError(f"expected a descriptor starting with 'D:', got {sddl[:40]!r}")
    if USERS_START_STOP_ACE in sddl:
        return sddl
    # The DACL runs from "D:" to the SACL ("S:") if there is one, else to the end.
    sacl_start = sddl.find("S:")
    if sacl_start == -1:
        return sddl + USERS_START_STOP_ACE
    return sddl[:sacl_start] + USERS_START_STOP_ACE + sddl[sacl_start:]


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str


def run_sc(arguments: list[str]) -> CommandResult:
    finished = subprocess.run(
        ["sc.exe", *arguments],
        capture_output=True,
        text=True,
        timeout=SC_TIMEOUT_SECONDS,
        check=False,
    )
    return CommandResult(finished.returncode, finished.stdout + finished.stderr)


def grant_service_rights(
    run: Callable[[list[str]], CommandResult] = run_sc,
    say: Callable[[str], None] = print,
) -> int:
    shown = run(["sdshow", SERVICE_NAME])
    if shown.returncode != 0:
        say(f"sc sdshow {SERVICE_NAME} failed ({shown.returncode}): {shown.stdout.strip()}")
        return EXIT_FAILED
    current = next((line.strip() for line in shown.stdout.splitlines() if line.strip()), "")
    try:
        wanted = add_users_start_stop(current)
    except SddlError as error:
        say(str(error))
        return EXIT_FAILED
    if wanted == current:
        say("Users may already start and stop the service")
        return EXIT_GRANTED
    written = run(["sdset", SERVICE_NAME, wanted])
    if written.returncode != 0:
        say(f"sc sdset {SERVICE_NAME} failed ({written.returncode}): {written.stdout.strip()}")
        return EXIT_FAILED
    say("Users may now start and stop the BajaWorker service")
    return EXIT_GRANTED
