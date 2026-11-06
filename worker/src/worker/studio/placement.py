"""Put a verified clip into the studio's clip folder without failing on Windows locks.

LIVESTACK-41 item 13. Seen live: the review UI said the studio "failed to
deliver the clip: [WinError 5] Access is denied: '…\\1\\1.mp4.part' ->
'…\\1\\1.mp4'", while the clip was in fact already there. The final rename
failed because the destination existed and was open: OBS/VLC playing it
from latest.m3u, or Defender/Search scanning it. Rules now:

  * Same content already there = delivered. Before replacing, compare the
    existing file's SHA-256 with the incoming one; equal → drop the new
    copy and report success. Hashes are cached (path, size, mtime), so a
    2 GB clip is not hashed again on every delivery.
  * Transient locks: retry the rename a few times with a short backoff
    (an antivirus or the indexer lets go within a second or two).
  * A file that stays locked with different content (a reprocessed clip
    while the old one is on air) is never overwritten: the new one goes
    beside it under its version name (enduro-042-p3-v2.mp4,
    shared.layout.versioned_clip_relative_path), the caller puts that name
    in latest.m3u, and the old one stays until nothing holds it.

`replace` and `sleep` are parameters so tests can play a Windows-like
filesystem (a destination that refuses to be replaced while "open").
"""

import json
import logging
import os
import threading
import time
from collections.abc import Callable
from pathlib import Path, PurePosixPath

from shared.hashing import sha256_of_file
from shared.layout import versioned_clip_relative_path
from worker.config import write_text_atomically

logger = logging.getLogger(__name__)

HASH_CACHE_NAME = ".clip-hashes.json"
# Pauses between rename attempts while the destination is locked (~3.7 s in all).
RENAME_BACKOFF_SECONDS = (0.2, 0.5, 1.0, 2.0)
# Version names tried beside a locked clip ("-v2" … "-v99").
MAX_VERSION = 99


class ClipHashCache:
    """SHA-256 of the clips in the folder, remembered by (size, modification time).

    A file whose size and mtime did not change has the same content, so its
    hash is read from .clip-hashes.json instead of reading the file again.
    """

    def __init__(self, root: Path, hash_file: Callable[[Path], str] = sha256_of_file) -> None:
        self.root = root
        self._hash_file = hash_file
        self._lock = threading.Lock()
        self._entries: dict[str, dict] = self._load()

    @property
    def _path(self) -> Path:
        return self.root / HASH_CACHE_NAME

    def _load(self) -> dict[str, dict]:
        try:
            entries = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return entries if isinstance(entries, dict) else {}

    def _key(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix()

    def sha256_of(self, path: Path) -> str | None:
        """The file's SHA-256, or None when it does not exist."""
        try:
            stat = path.stat()
        except FileNotFoundError:
            return None
        key = self._key(path)
        with self._lock:
            entry = self._entries.get(key)
        unchanged = entry and (entry.get("size"), entry.get("mtime_ns")) == (
            stat.st_size,
            stat.st_mtime_ns,
        )
        if unchanged:
            return entry["sha256"]
        sha256 = self._hash_file(path)
        self._store(path, sha256)
        return sha256

    def remember(self, path: Path, sha256: str) -> None:
        """A file we just placed: its hash is known without reading it."""
        self._store(path, sha256)

    def _store(self, path: Path, sha256: str) -> None:
        stat = path.stat()
        with self._lock:
            self._entries[self._key(path)] = {
                "size": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
                "sha256": sha256,
            }
            write_text_atomically(self._path, json.dumps(self._entries, indent=1))


class ClipPlacer:
    def __init__(
        self,
        root: Path,
        hashes: ClipHashCache,
        *,
        replace: Callable[[Path, Path], None] = os.replace,
        sleep: Callable[[float], None] = time.sleep,
        backoff_seconds: tuple[float, ...] = RENAME_BACKOFF_SECONDS,
    ) -> None:
        self.root = root
        self.hashes = hashes
        self._replace = replace
        self._sleep = sleep
        self._backoff_seconds = backoff_seconds
        # One placement at a time: the receiver and the deliver task may
        # finish the same clip at the same moment (a double delivery).
        self._lock = threading.Lock()

    def _absolute(self, relative_path: str) -> Path:
        return self.root.joinpath(*PurePosixPath(relative_path).parts)

    def versions_of(self, relative_path: str) -> list[str]:
        """The clip's name and its -v2 … names that exist on disk, in order."""
        found = []
        for version in range(1, MAX_VERSION + 1):
            candidate = versioned_clip_relative_path(relative_path, version)
            if self._absolute(candidate).exists():
                found.append(candidate)
        return found

    def find_same_content(self, relative_path: str, sha256: str) -> str | None:
        """Which name (base or -vN) already holds exactly this clip, if any."""
        for candidate in self.versions_of(relative_path):
            if self.hashes.sha256_of(self._absolute(candidate)) == sha256:
                return candidate
        return None

    def place(self, staged: Path, relative_path: str, sha256: str) -> str:
        """Move the verified file `staged` into place; returns the name it got.

        Raises OSError only when no name at all could be written (a real
        disk problem, not a lock).
        """
        with self._lock:
            already = self.find_same_content(relative_path, sha256)
            if already is not None:
                staged.unlink(missing_ok=True)
                logger.info("%s already holds this clip; nothing to replace", already)
                return already
            last_error: OSError | None = None
            for version in range(1, MAX_VERSION + 1):
                candidate = versioned_clip_relative_path(relative_path, version)
                destination = self._absolute(candidate)
                if version > 1 and destination.exists():
                    continue  # an older version beside it: never overwrite that
                try:
                    self._replace_with_retries(staged, destination)
                except PermissionError as locked:
                    # Still open after the retries (on air): try the next
                    # version name beside it instead of overwriting.
                    logger.warning("%s stays locked (%s); writing beside it", candidate, locked)
                    last_error = locked
                    continue
                self.hashes.remember(destination, sha256)
                if version > 1:
                    logger.info("the clip on air is locked; new version stored as %s", candidate)
                return candidate
            raise last_error or OSError(f"no free name for {relative_path}")

    def _replace_with_retries(self, staged: Path, destination: Path) -> None:
        for pause in (*self._backoff_seconds, None):
            try:
                self._replace(staged, destination)
                return
            except PermissionError:
                if pause is None:
                    raise
                self._sleep(pause)
