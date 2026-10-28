"""`BajaWorker.exe --check`: is this build complete? Exit 0 = yes.

Run by CI on the frozen exe and useful on a volunteer's PC. It imports
every bundled module (a module PyInstaller missed fails here, not in the
middle of an event), checks the CA bundle httpx needs for the HTTPS
enrollment call, runs the bundled ffmpeg and ffprobe, and reports which
video encoder the worker would use. A missing GPU is not a failure: the
worker falls back to libx264 (design §9).
"""

import importlib
import os
import subprocess
from collections.abc import Callable, Iterable
from pathlib import Path

from baja_installer.layout import bundled_ffmpeg, bundled_ffprobe, bundled_nssm
from shared.ffmpeg import detect_encoder

TOOL_TIMEOUT_SECONDS = 30


def failed_imports(module_names: Iterable[str]) -> list[str]:
    """One line, '<module>: <error>', per module that cannot be imported."""
    failures = []
    for name in module_names:
        try:
            importlib.import_module(name)
        except Exception as error:  # any import-time error counts as missing
            failures.append(f"{name}: {type(error).__name__}: {error}")
    return failures


def tool_version_line(tool: Path, expected_name: str) -> str:
    """First line of `<tool> -version`; raises RuntimeError if it does not
    run or does not say it is `expected_name`."""
    try:
        finished = subprocess.run(
            [str(tool), "-hide_banner", "-version"],
            capture_output=True,
            text=True,
            timeout=TOOL_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RuntimeError(f"{tool} did not run: {error}") from error
    first_line = finished.stdout.splitlines()[0] if finished.stdout else ""
    if finished.returncode != 0 or not first_line.startswith(f"{expected_name} version"):
        raise RuntimeError(f"{tool} -version exited {finished.returncode}: {first_line!r}")
    return first_line


def run_check(
    install: Path, module_names: Iterable[str], say: Callable[[str], None] = print
) -> int:
    problems: list[str] = []

    module_names = list(module_names)
    import_problems = failed_imports(module_names)
    problems.extend(f"import {problem}" for problem in import_problems)
    say(f"modules: {len(module_names) - len(import_problems)}/{len(module_names)} import")

    try:
        import certifi

        if not Path(certifi.where()).is_file():
            problems.append(f"CA bundle missing at {certifi.where()}")
    except ImportError as error:
        problems.append(f"certifi: {error}")

    for tool, name in ((bundled_ffmpeg(install), "ffmpeg"), (bundled_ffprobe(install), "ffprobe")):
        try:
            say(f"{name}: {tool_version_line(tool, name)}")
        except RuntimeError as error:
            problems.append(str(error))

    if not problems:
        say(f"video encoder: {detect_encoder(str(bundled_ffmpeg(install)))}")

    if os.name == "nt" and not bundled_nssm(install).is_file():
        problems.append(f"{bundled_nssm(install)} is missing")

    for problem in problems:
        say(f"PROBLEM: {problem}")
    say("check passed" if not problems else f"check FAILED ({len(problems)} problems)")
    return 0 if not problems else 1
