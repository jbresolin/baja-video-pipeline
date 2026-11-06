"""The working folder and the storage cap (design §9, docs/api.md §8.3).

    <working folder>/
        raws/<sender>-<YYYYMMDD-HHMMSS>-<job id first 8>.<ext>
                              held raws (readable names, LIVESTACK-41 item 12;
                              "<job_id>.<ext>" for older jobs): served to
                              peers, never evicted
        raws/.raw-names.json  which job each readable name belongs to
        clips/<job_id>.mp4    the clip being rendered/delivered (+ a .json sidecar)
        clips/<job_id>.<sha256>.mp4   delivered clips, kept (worker.clip_store)
        clips/<prova>/<car>/<passagem>[.vN].mp4   the master's clip archive
        tmp/                  proxies, thumbnails, overlays while rendering
        logs/worker.log

Half-written files are hidden dot-files (".<name>.part"), so a name without
a leading dot is always complete. Everything under raws/, clips/ and tmp/
counts against the cap, including partial files.

Held raws are never deleted to make room. They go only when the
orchestrator lists them in `deletable_job_ids` (the job was discarded).

Raw names: the orchestrator says what to call a raw (DownloadRawTask.
raw_file_name, built from the sender and the time the bot received it),
so a file copied out of raws/ still says what it is. A readable name only
carries the first 8 characters of the job id, so the full id of every
such file is kept in the hidden raws/.raw-names.json (written atomically
when a download starts). Raws downloaded before the upgrade keep their
"<job_id>.<ext>" name and are found by it, as before.
"""

import json
import logging
import shutil
import threading
import time
from collections import Counter
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

from worker.clip_store import ClipStore
from worker.config import write_text_atomically

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
# shared.http_transfer.partial_path_for: "abc.mp4" downloads into ".abc.mp4.part".
PARTIAL_SUFFIX = ".part"
# Readable raw name → full job id (hidden: peers never see it, the cleanup skips it).
RAW_NAMES_INDEX = ".raw-names.json"

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
        # Delivered clips the worker keeps, and the master's archive.
        self.clips = ClipStore(self.clips_dir)
        # Jobs whose raw a task is using right now, with a count because
        # make_proxy and process may use the same raw at the same time.
        self._pinned_job_ids: Counter[str] = Counter()
        self._lock = threading.Lock()
        self._raw_names: dict[str, str] = self._load_raw_names()

    # --- Raw names ---

    @property
    def _raw_names_path(self) -> Path:
        return self.raws_dir / RAW_NAMES_INDEX

    def _load_raw_names(self) -> dict[str, str]:
        try:
            names = json.loads(self._raw_names_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError):
            logger.warning("raws/%s is unreadable; readable names are not mapped", RAW_NAMES_INDEX)
            return {}
        if not isinstance(names, dict):
            return {}
        return {str(name): str(job) for name, job in names.items()}

    def _save_raw_names_locked(self) -> None:
        write_text_atomically(self._raw_names_path, json.dumps(self._raw_names, indent=1) + "\n")

    def job_id_of_raw_file(self, file_name: str) -> str:
        """Which job a file in raws/ belongs to, partial files included.

        "joao-…-2f2510a3.mp4" and ".joao-…-2f2510a3.mp4.part(.json)" → the full
        id from the index; an older "<job_id>.<ext>" → that id.
        """
        base = file_name.removeprefix(".")
        for suffix in (".part.json", PARTIAL_SUFFIX):
            if file_name.startswith(".") and base.endswith(suffix):
                base = base.removesuffix(suffix)
                break
        with self._lock:
            known = self._raw_names.get(base)
        return known if known is not None else _job_id_of(file_name)

    # --- Paths ---

    def raw_path(self, job_id: str, extension: str, file_name: str | None = None) -> Path:
        """Where a raw is stored.

        `file_name` is the readable name from the offer (item 12); it is
        remembered in the index before anything is written, so even a
        partial download is found again by job id. Without one (older
        jobs), "<job_id><extension>"; `extension` includes the dot.
        """
        if file_name and _is_plain_file_name(file_name):
            with self._lock:
                if self._raw_names.get(file_name) != job_id:
                    self._raw_names[file_name] = job_id
                    self._save_raw_names_locked()
            return self.raws_dir / file_name
        return self.raws_dir / f"{job_id}{extension}"

    def find_raw(self, job_id: str) -> Path | None:
        """The complete raw for `job_id`, or None when we do not hold it."""
        for path in self._complete_files(self.raws_dir):
            if self.job_id_of_raw_file(path.name) == job_id:
                return path
        return None

    def partial_raw_destination(self, job_id: str) -> Path | None:
        """Final path of an unfinished download of `job_id`, if one is on disk.

        ".abc.mov.part" → raws/abc.mov, so a re-offered download resumes it.
        """
        for path in self.raws_dir.iterdir():
            name = path.name
            if (
                name.startswith(".")
                and name.endswith(PARTIAL_SUFFIX)
                and self.job_id_of_raw_file(name) == job_id
            ):
                return self.raws_dir / name.removeprefix(".").removesuffix(PARTIAL_SUFFIX)
        return None

    def held_job_ids(self) -> list[str]:
        """Every job whose complete raw is on disk (for heartbeats)."""
        return sorted(
            self.job_id_of_raw_file(path.name) for path in self._complete_files(self.raws_dir)
        )

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
        targets = [
            p
            for p in self.raws_dir.iterdir()
            if p.name != RAW_NAMES_INDEX and self.job_id_of_raw_file(p.name) == job_id
        ]
        for path in targets:
            try:
                path.unlink(missing_ok=True)
            except PermissionError:
                # Windows refuses while a peer is still downloading it.
                logger.warning("could not delete %s yet (file in use)", path.name)
                return False
        if targets:
            logger.info("deleted raw of job %s", job_id, extra={"job_id": job_id})
        with self._lock:
            names = [name for name, owner in self._raw_names.items() if owner == job_id]
            for name in names:
                del self._raw_names[name]
            if names:
                self._save_raw_names_locked()
        return True

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
            is_partial = path.name.startswith(".") and path.name != RAW_NAMES_INDEX
            if is_partial and now - path.stat().st_mtime > STALE_PARTIAL_AGE_SECONDS:
                logger.info("removing stale partial download %s", path.name)
                path.unlink(missing_ok=True)

    @staticmethod
    def _complete_files(folder: Path) -> list[Path]:
        return [p for p in folder.iterdir() if p.is_file() and not p.name.startswith(".")]


def _job_id_of(file_name: str) -> str:
    """ "abc.mp4" → "abc", ".abc.mp4.part" → "abc". Job ids never contain dots."""
    return file_name.removeprefix(".").split(".", 1)[0]


def _is_plain_file_name(file_name: str) -> bool:
    """A name, not a path, and not hidden: anything else is ignored (the offer
    comes from the orchestrator, but a file name must never pick a folder)."""
    return (
        file_name == Path(file_name).name
        and "/" not in file_name
        and "\\" not in file_name
        and not file_name.startswith(".")
        and file_name not in ("", "..")
    )
