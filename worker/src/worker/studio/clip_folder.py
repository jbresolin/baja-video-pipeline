"""The folder the studio's OBS plays clips from: chosen at setup, readable by OBS.

The installer locks the working folder (C:\\BajaWorker) to SYSTEM and
Administrators, because worker.json holds the worker's bearer token
(design §17). The old default clip folder, <working folder>\\box, sat
inside it, so OBS, which runs as the logged-in user, could not open the
clips. Now:

  * a studio is asked for its clip folder on the setup page right after
    enrollment (default C:\\BajaClips), before the receiver starts;
  * the worker creates it if missing and grants the local Users group
    read access, inherited by every clip and by latest.m3u, every time the
    studio starts (cheap, and it repairs a folder someone re-created).

The service runs as LocalSystem, which may change any folder's ACL.
"""

import logging
import os
import subprocess
from collections.abc import Callable
from pathlib import Path, PureWindowsPath

from shared.layout import LATEST_PLAYLIST_NAME

logger = logging.getLogger(__name__)

DEFAULT_WINDOWS_CLIP_ROOT = PureWindowsPath(r"C:\BajaClips")
# The well-known SID of the local "Users" group (BUILTIN\Users). The SID,
# not the name: on a Portuguese Windows the group is called "Usuários".
USERS_GROUP_SID = "*S-1-5-32-545"
# (OI)(CI): inherited by files and subfolders; RX: read and execute (list, open).
USERS_READ_GRANT = f"{USERS_GROUP_SID}:(OI)(CI)RX"

CommandRunner = Callable[[list[str]], subprocess.CompletedProcess]


def suggested_clip_root(working_folder: Path) -> str:
    """What the setup form proposes: C:\\BajaClips on Windows, else <working folder>/box."""
    if os.name == "nt":
        return str(DEFAULT_WINDOWS_CLIP_ROOT)
    return str(working_folder / "box")


def playlist_path(clip_root: Path) -> Path:
    """The full path of latest.m3u, which the status page shows for OBS."""
    return clip_root / LATEST_PLAYLIST_NAME


def _run(arguments: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        arguments,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
    )


def grant_users_read_command(clip_root: Path) -> list[str]:
    """icacls arguments that let BUILTIN\\Users read the folder and everything in it."""
    return ["icacls", str(clip_root), "/grant", USERS_READ_GRANT, "/T", "/C", "/Q"]


def prepare_clip_root(
    clip_root: Path,
    *,
    windows: bool | None = None,
    run: CommandRunner = _run,
) -> str | None:
    """Create the folder and (Windows) grant Users read access.

    Returns None when all went well, else a short pt-BR sentence for the
    status page. A failed grant is not fatal: the studio still receives
    clips, OBS just may not read them, and the page says so.
    """
    windows = os.name == "nt" if windows is None else windows
    try:
        clip_root.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        logger.error("cannot create the studio clip folder %s: %s", clip_root, error)
        return f"Não consegui criar a pasta {clip_root} ({error.strerror or error})."
    if not windows:
        return None
    try:
        finished = run(grant_users_read_command(clip_root))
    except (OSError, subprocess.TimeoutExpired) as error:
        finished = subprocess.CompletedProcess([], 1, "", str(error))
    if finished.returncode != 0:
        detail = (finished.stderr or finished.stdout or "").strip()
        logger.error("could not grant Users read access on %s: %s", clip_root, detail)
        return f"Não consegui liberar a leitura da pasta para o OBS ({detail or 'icacls falhou'})."
    logger.info("Users may read the studio clip folder %s", clip_root)
    return None
