"""The studio's clip folder ("box root") and its latest.m3u (design §7, api.md §8.1).

    <box root>/
        latest.m3u                  newest first, at most 50, relative paths
        .playlist-index.json        what latest.m3u was built from (hidden)
        <prova>/<car>/<passagem>.mp4

Clips arrive two ways, both writing a hidden ".part" file beside the final
path and renaming it when verified: the receiver (direct push) and the
deliver task (outbox pull). Either way, `record_clip` then puts the clip
at the top of the playlist; a reprocessed clip keeps its path and simply
moves back to the top.

The playlist titles need the prova's display name and the team, which a
path like "enduro/42/3.mp4" does not carry. The caller passes them from
GET /jobs/{id}; when that is unavailable, they are guessed from the path.
"""

import json
import logging
import threading
from pathlib import Path, PurePosixPath

from shared.layout import (
    DEFAULT_PLAYLIST_LENGTH,
    LATEST_PLAYLIST_NAME,
    PlaylistEntry,
    is_safe_relative_path,
    render_latest_playlist,
)
from shared.models import Job
from worker.config import write_text_atomically

logger = logging.getLogger(__name__)

PLAYLIST_INDEX_NAME = ".playlist-index.json"


class UnsafeClipPathError(ValueError):
    pass


class StudioLibrary:
    def __init__(self, root: Path, max_playlist_entries: int = DEFAULT_PLAYLIST_LENGTH) -> None:
        self.root = root
        self.max_playlist_entries = max_playlist_entries
        self.root.mkdir(parents=True, exist_ok=True)
        # The receiver and the deliver task may finish clips at the same time.
        self._lock = threading.Lock()

    @property
    def playlist_path(self) -> Path:
        return self.root / LATEST_PLAYLIST_NAME

    def absolute_path(self, relative_path: str) -> Path:
        """Where a clip goes; refuses paths that could leave the box root."""
        if not is_safe_relative_path(relative_path):
            raise UnsafeClipPathError(f"unsafe clip path {relative_path!r}")
        destination = (self.root / relative_path).resolve()
        # Belt and braces: also refuse anything that resolves outside the
        # root (e.g. through a symlinked folder).
        if not destination.is_relative_to(self.root.resolve()):
            raise UnsafeClipPathError(f"clip path {relative_path!r} leaves the box root")
        return destination

    def record_clip(self, relative_path: str, job: Job | None) -> PlaylistEntry:
        """Put the clip at the top of latest.m3u (rewritten atomically)."""
        entry = playlist_entry_for(relative_path, job)
        with self._lock:
            entries = [e for e in self._load_index() if e.relative_path != relative_path]
            entries.insert(0, entry)
            entries = entries[: self.max_playlist_entries]
            index_text = json.dumps([_entry_to_dict(e) for e in entries], ensure_ascii=False)
            write_text_atomically(self.root / PLAYLIST_INDEX_NAME, index_text)
            playlist_text = render_latest_playlist(entries, self.max_playlist_entries)
            write_text_atomically(self.playlist_path, playlist_text)
        logger.info("latest.m3u now starts with %s", relative_path)
        return entry

    def _load_index(self) -> list[PlaylistEntry]:
        index_path = self.root / PLAYLIST_INDEX_NAME
        if not index_path.exists():
            return []
        try:
            raw_entries = json.loads(index_path.read_text(encoding="utf-8"))
            return [PlaylistEntry(**item) for item in raw_entries]
        except (ValueError, TypeError):
            logger.warning("playlist index is unreadable; starting a new one")
            return []


def playlist_entry_for(relative_path: str, job: Job | None) -> PlaylistEntry:
    """Title fields from the job when known, else from "<prova>/<car>/<passagem>.mp4"."""
    if job is not None and job.prova and job.car_number is not None and job.passagem:
        return PlaylistEntry(relative_path, job.prova, job.car_number, job.passagem, job.team_name)
    parts = PurePosixPath(relative_path).parts
    try:
        prova_slug, car_text, file_name = parts[-3], parts[-2], parts[-1]
        car_number, passagem = int(car_text), int(PurePosixPath(file_name).stem)
    except (IndexError, ValueError):
        return PlaylistEntry(relative_path, relative_path, 0, 1)
    return PlaylistEntry(relative_path, prova_slug.replace("-", " ").title(), car_number, passagem)


def _entry_to_dict(entry: PlaylistEntry) -> dict:
    return {
        "relative_path": entry.relative_path,
        "prova": entry.prova,
        "car_number": entry.car_number,
        "passagem": entry.passagem,
        "team_name": entry.team_name,
    }
