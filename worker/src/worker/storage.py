"""The working folder and the storage cap (design §9, docs/api.md §8.3).

    <working folder>/
        raws/<job_id>.<ext>   held raws: served to peers, never evicted
        clips/<job_id>.mp4    rendered clips until delivered (+ a .json sidecar)
        tmp/                  proxies, thumbnails, overlays while rendering
        logs/worker.log

Half-written files are hidden dot-files (".<name>.part"), so a name without
a leading dot is always complete. Everything under raws/, clips/ and tmp/
counts against the cap, including partial files.

Held raws are never deleted to make room. They go only when the
orchestrator lists them in `deletable_job_ids` (the job was discarded).
"""

import logging
import shutil
import threading
import time
from collections import Counter
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

logger = logging.getLogger(__name__)

RAWS_FOLDER = "raws"
CLIPS_FOLDER = "clips"
TMP_FOLDER = "tmp"
LOGS_FOLDER = "logs"

# Never fill the disk completely, whatever the cap says: Windows and the
# volunteer's own programs need room too.
DISK_RESERVE_BYTES = 2 * 1024**3
# Partial downloads nobody resumed for this long are thrown away at start.
STALE_PARTIAL_AGE_SECONDS = 24 * 3600

# Returns the free bytes on the disk that holds the given folder.
DiskFreeFunction = Callable[[Path], int]


def _disk_free_bytes(folder: Path) -> int:
    return shutil.disk_usage(folder).free


class Storage:
    def __init__(
        self,
        root: Path,
        cap_bytes: int,
        *,
        ignores_cap_for_raws: bool = False,
        disk_free: DiskFreeFunction = _disk_free_bytes,
    ) -> None:
        """`ignores_cap_for_raws=True` is the master: it holds every raw
        (design §4), so only the real disk space can stop it."""
        self.root = root
        self.cap_bytes = cap_bytes
        self.ignores_cap_for_raws = ignores_cap_for_raws
        self._disk_free = disk_free
        self.raws_dir = root / RAWS_FOLDER
        self.clips_dir = root / CLIPS_FOLDER
        self.tmp_dir = root / TMP_FOLDER
        for folder in (self.raws_dir, self.clips_dir, self.tmp_dir):
            folder.mkdir(parents=True, exist_ok=True)
        # Jobs whose raw a task is using right now, with a count because
        # make_proxy and process may use the same raw at the same time.
        self._pinned_job_ids: Counter[str] = Counter()
        self._lock = threading.Lock()

    # --- Paths ---

    def raw_path(self, job_id: str, extension: str) -> Path:
        """Where a raw is stored; `extension` includes the dot, e.g. ".mp4"."""
        return self.raws_dir / f"{job_id}{extension}"

    def find_raw(self, job_id: str) -> Path | None:
        """The complete raw for `job_id`, or None when we do not hold it."""
        for path in self._complete_files(self.raws_dir):
            if _job_id_of(path.name) == job_id:
                return path
        return None

    def held_job_ids(self) -> list[str]:
        """Every job whose complete raw is on disk (for heartbeats)."""
        return sorted(_job_id_of(path.name) for path in self._complete_files(self.raws_dir))

    def clip_path(self, job_id: str) -> Path:
        return self.clips_dir / f"{job_id}.mp4"

    def clip_sidecar_path(self, job_id: str) -> Path:
        """Notes which inputs `clip_path(job_id)` was rendered from."""
        return self.clips_dir / f"{job_id}.json"

    def tmp_path(self, file_name: str) -> Path:
        return self.tmp_dir / file_name

    # --- Space ---

    def used_bytes(self) -> int:
        """Bytes in raws/, clips/ and tmp/, partial files included."""
        total = 0
        for folder in (self.raws_dir, self.clips_dir, self.tmp_dir):
            for path in folder.rglob("*"):
                try:
                    if path.is_file():
                        total += path.stat().st_size
                except FileNotFoundError:
                    pass  # deleted while we were counting
        return total

    def disk_free_bytes(self) -> int:
        return max(0, self._disk_free(self.root) - DISK_RESERVE_BYTES)

    def free_bytes_under_cap(self) -> int:
        """What we report in heartbeats: room left under the cap and on disk."""
        return max(0, min(self.cap_bytes - self.used_bytes(), self.disk_free_bytes()))

    def can_hold_new_raw(self, size_bytes: int) -> bool:
        """Is there room to download one more raw of this size?"""
        if self.ignores_cap_for_raws:
            return size_bytes <= self.disk_free_bytes()
        return size_bytes <= self.free_bytes_under_cap()

    # --- Pins and deletion ---

    @contextmanager
    def pin(self, job_id: str) -> Iterator[None]:
        """While inside this block, delete_raw(job_id) refuses to delete."""
        with self._lock:
            self._pinned_job_ids[job_id] += 1
        try:
            yield
        finally:
            with self._lock:
                self._pinned_job_ids[job_id] -= 1
                if self._pinned_job_ids[job_id] <= 0:
                    del self._pinned_job_ids[job_id]

    def is_pinned(self, job_id: str) -> bool:
        with self._lock:
            return self._pinned_job_ids[job_id] > 0

    def delete_raw(self, job_id: str) -> bool:
        """Delete the raw (and any partial download) unless a task uses it.

        Returns True when nothing of the raw is left on disk.
        """
        if self.is_pinned(job_id):
            logger.info("raw %s is in use; will delete it on a later heartbeat", job_id)
            return False
        targets = [p for p in self.raws_dir.iterdir() if _belongs_to_job(p.name, job_id)]
        for path in targets:
            try:
                path.unlink(missing_ok=True)
            except PermissionError:
                # Windows refuses while a peer is still downloading it.
                logger.warning("could not delete %s yet (file in use)", path.name)
                return False
        if targets:
            logger.info("deleted raw of job %s", job_id, extra={"job_id": job_id})
        return True

    def delete_partial_raws(self, job_id: str) -> None:
        """Remove an unfinished download of `job_id` (lease lost: discard it)."""
        for path in self.raws_dir.iterdir():
            if path.name.startswith(".") and _belongs_to_job(path.name, job_id):
                path.unlink(missing_ok=True)

    def delete_clip(self, job_id: str) -> None:
        self.clip_sidecar_path(job_id).unlink(missing_ok=True)
        self.clip_path(job_id).unlink(missing_ok=True)

    def clean_up_at_start(self, now: float | None = None) -> None:
        """Empty tmp/ and drop partial raws nobody resumed for a day.

        Clips are kept: a crashed worker that is offered the same job again
        reuses the finished render instead of rendering it twice.
        """
        now = now if now is not None else time.time()
        for path in self.tmp_dir.iterdir():
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
            else:
                path.unlink(missing_ok=True)
        for path in self.raws_dir.iterdir():
            is_partial = path.name.startswith(".")
            if is_partial and now - path.stat().st_mtime > STALE_PARTIAL_AGE_SECONDS:
                logger.info("removing stale partial download %s", path.name)
                path.unlink(missing_ok=True)

    @staticmethod
    def _complete_files(folder: Path) -> list[Path]:
        return [p for p in folder.iterdir() if p.is_file() and not p.name.startswith(".")]


def _job_id_of(file_name: str) -> str:
    """ "abc.mp4" → "abc", ".abc.mp4.part" → "abc". Job ids never contain dots."""
    return file_name.removeprefix(".").split(".", 1)[0]


def _belongs_to_job(file_name: str, job_id: str) -> bool:
    """True for "<job>.<ext>", ".<job>.<ext>.part" and ".<job>.<ext>.part.json"."""
    return _job_id_of(file_name) == job_id
