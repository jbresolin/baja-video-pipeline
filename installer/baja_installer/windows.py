"""The few Windows-only calls, kept together so the rest stays testable.

Nothing here runs in the Linux test suite except through fakes; the
Windows CI job exercises it on a real runner (.github/workflows/installer.yml).
"""

import contextlib
import ctypes
import os
import subprocess
import sys
import webbrowser
from pathlib import Path

from baja_installer.layout import SERVICE_NAME, bundled_nssm, install_folder

# ShellExecuteW returns a value above 32 on success.
SHELL_EXECUTE_SUCCESS_ABOVE = 32
SHOW_NORMAL = 1
SERVICE_COMMAND_TIMEOUT_SECONDS = 90


def is_elevated() -> bool:
    if os.name != "nt":
        return os.geteuid() == 0
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False


def relaunch_elevated(arguments: list[str]) -> bool:
    """Start this exe again with `arguments` behind a UAC prompt.
    False if the volunteer said no (or this is not Windows)."""
    if os.name != "nt":
        return False
    parameters = subprocess.list2cmdline(arguments)
    result = ctypes.windll.shell32.ShellExecuteW(
        None, "runas", sys.executable, parameters, None, SHOW_NORMAL
    )
    return result > SHELL_EXECUTE_SUCCESS_ABOVE


def open_url_as_desktop_user(url: str) -> None:
    """Open `url` in the volunteer's normal (not elevated) browser.

    We usually run elevated here. Handing the URL to explorer.exe makes the
    already-running desktop shell open it, so the browser does not inherit
    our administrator rights.
    """
    if os.name == "nt":
        subprocess.Popen(["explorer.exe", url])
    else:
        webbrowser.open(url)


def restart_service(install: Path | None = None) -> None:
    """`nssm restart BajaWorker`; raises CalledProcessError if it fails."""
    nssm = bundled_nssm(install or install_folder())
    subprocess.run(
        [str(nssm), "restart", SERVICE_NAME],
        check=True,
        capture_output=True,
        timeout=SERVICE_COMMAND_TIMEOUT_SECONDS,
    )


def ask_yes_no(question: str) -> bool:
    try:
        answer = input(f"{question} [s/N] ")
    except EOFError:
        return False
    return answer.strip().lower() in ("s", "sim", "y", "yes")


def wait_for_enter() -> None:
    with contextlib.suppress(EOFError):  # no console attached
        input("Pressione Enter para fechar esta janela.")
