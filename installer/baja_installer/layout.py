"""Names and paths shared by the exe, the Inno Setup script and the docs.

Install folder (C:\\Program Files\\BajaWorker), written by the installer:
    BajaWorker.exe        this program (the worker and the commands in cli.py)
    _internal\\            Python and the bundled libraries (PyInstaller onedir)
    ffmpeg\\               ffmpeg.exe, ffprobe.exe and their DLLs (BtbN build)
    nssm.exe              the service wrapper

Data folder (C:\\BajaWorker by default), see worker/README.md:
    worker.json, tailscale-authkey.txt (briefly), logs\\, raws\\, clips\\, tmp\\
"""

import os
import sys
from pathlib import Path

SERVICE_NAME = "BajaWorker"
SETUP_PAGE_URL = "http://localhost:8700"
DATA_FOLDER_NAME = "BajaWorker"
CONFIG_FILE_NAME = "worker.json"
FFMPEG_FOLDER_NAME = "ffmpeg"
# Tailscale's MSI installs here by default (worker_join.md §1).
WINDOWS_TAILSCALE_EXE = Path(r"C:\Program Files\Tailscale\tailscale.exe")


def executable_name(stem: str) -> str:
    return f"{stem}.exe" if os.name == "nt" else stem


def install_folder() -> Path:
    """The folder BajaWorker.exe lives in; from source, installer/."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def bundled_ffmpeg(install: Path) -> Path:
    return install / FFMPEG_FOLDER_NAME / executable_name("ffmpeg")


def bundled_ffprobe(install: Path) -> Path:
    return install / FFMPEG_FOLDER_NAME / executable_name("ffprobe")


def bundled_nssm(install: Path) -> Path:
    return install / "nssm.exe"


def default_config_path() -> Path:
    """C:\\BajaWorker\\worker.json on Windows (on the system drive), else
    ./worker.json. The installer always passes --config explicitly."""
    if os.name == "nt":
        system_drive = os.environ.get("SYSTEMDRIVE", "C:")  # Windows env names ignore case
        return Path(system_drive + "\\") / DATA_FOLDER_NAME / CONFIG_FILE_NAME
    return Path(CONFIG_FILE_NAME)
