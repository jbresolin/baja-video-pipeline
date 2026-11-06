"""The studio's clip folder ("box root") and its latest.m3u (design §7, api.md §8.1).

    <box root>/
        latest.m3u                  newest first, at most 50, relative paths
        .playlist-index.json        what latest.m3u was built from (hidden)
        <prova>/<car>/<prova>-<car 3 digits>-p<passagem>.mp4   (shared.layout)

Clips arrive two ways, both writing a hidden file beside the final path
and moving it into place when verified (`place_clip`, worker.studio.
placement): the receiver (direct push) and the deliver task (outbox pull).
A clip already there with the same content counts as delivered; a locked
clip on air with different content gets the new version beside it
(…-v2.mp4). Either way, `record_clip` then puts the clip at the top of the
playlist, in place of any older version of the same passagem.

The playlist titles need the prova's display name and the team, which a
path like "enduro/42/3.mp4" does not carry. The caller passes them from
GET /jobs/{id}; when that is unavailable, they are guessed from the path.
"""

import json
import logging
import re
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
from worker.studio.placement import ClipHashCache, ClipPlacer

logger = logging.getLogger(__name__)

PLAYLIST_INDEX_NAME = ".playlist-index.json"
# shared.layout.clip_file_stem / versioned_clip_relative_path: "…-042-p3[-v2]".
READABLE_CLIP_STEM = re.compile(r".*-\d+-p(\d+)(?:-v\d+)?")


class UnsafeClipPathError(ValueError):
    pass


class StudioLibrary:
    def __init__(
        self,
        root: Path,
        max_playlist_entries: int = DEFAULT_PLAYLIST_LENGTH,
        *,
        placer: ClipPlacer | None = None,
    ) -> None:
        """`placer`: tests pass one with a Windows-like fake filesystem."""
        self.root = root
        self.max_playlist_entries = max_playlist_entries
        self.root.mkdir(parents=True, exist_ok=True)
        self.placer = placer or ClipPlacer(root, ClipHashCache(root))
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

    def find_same_content(self, relative_path: str, sha256: str) -> str | None:
        """The name (base or -vN) that already holds exactly this clip, if any."""
        return self.placer.find_same_content(relative_path, sha256)

    def place_clip(self, staged: Path, relative_path: str, sha256: str) -> str:
        """Move a verified file into place; returns the name it got (see module doc)."""
        self.absolute_path(relative_path)  # refuses unsafe paths
        return self.placer.place(staged, relative_path, sha256)

    def sha256_of(self, relative_path: str) -> str | None:
        """Cached SHA-256 of a clip in the folder (None if missing)."""
        return self.placer.hashes.sha256_of(self.absolute_path(relative_path))

    def record_clip(self, relative_path: str, job: Job | None) -> PlaylistEntry:
        """Put the clip at the top of latest.m3u (rewritten atomically).

        An older version of the same passagem (same folder and passagem,
        e.g. enduro-042-p3.mp4 when enduro-042-p3-v2.mp4 arrives) leaves the
        playlist, and its file is deleted once nothing holds it any more
        (tried again with every later clip in that folder).
        """
        entry = playlist_entry_for(relative_path, job)
        with self._lock:
            entries = [
                e
                for e in self._load_index()
                if e.relative_path != relative_path and not _same_passagem(e, entry)
            ]
            entries.insert(0, entry)
            entries = entries[: self.max_playlist_entries]
            index_text = json.dumps([_entry_to_dict(e) for e in entries], ensure_ascii=False)
            write_text_atomically(self.root / PLAYLIST_INDEX_NAME, index_text)
            playlist_text = render_latest_playlist(entries, self.max_playlist_entries)
            write_text_atomically(self.playlist_path, playlist_text)
            self._delete_superseded_versions(entry, {e.relative_path for e in entries})
        logger.info("latest.m3u now starts with %s", relative_path)
        return entry

    def _delete_superseded_versions(self, newest: PlaylistEntry, in_playlist: set[str]) -> None:
        """Old versions of `newest`'s passagem that nothing holds open any more."""
        folder = PurePosixPath(newest.relative_path).parent
        for path in self.absolute_path(newest.relative_path).parent.glob("*.mp4"):
            relative = str(folder / path.name)
            if relative in in_playlist or path.name.startswith("."):
                continue
            try:
                passagem = _passagem_from_file_name(path.name)
            except ValueError:
                continue
            if passagem != newest.passagem:
                continue
            try:
                path.unlink()
                logger.info("deleted %s, replaced by %s", relative, newest.relative_path)
            except PermissionError:
                logger.info("%s is still open (on air?); deleting it later", relative)

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
        car_number, passagem = int(car_text), _passagem_from_file_name(file_name)
    except (IndexError, ValueError):
        return PlaylistEntry(relative_path, relative_path, 0, 1)
    return PlaylistEntry(relative_path, prova_slug.replace("-", " ").title(), car_number, passagem)


def _passagem_from_file_name(file_name: str) -> int:
    """ "enduro-042-p3.mp4" / "enduro-042-p3-v2.mp4" → 3; an older "3.mp4" → 3."""
    stem = PurePosixPath(file_name).stem
    match = READABLE_CLIP_STEM.fullmatch(stem)
    return int(match.group(1)) if match else int(stem)


def _same_passagem(a: PlaylistEntry, b: PlaylistEntry) -> bool:
    """Two versions of one passagem: same prova/car folder and passagem number."""
    same_folder = PurePosixPath(a.relative_path).parent == PurePosixPath(b.relative_path).parent
    return same_folder and a.passagem == b.passagem


def _entry_to_dict(entry: PlaylistEntry) -> dict:
    return {
        "relative_path": entry.relative_path,
        "prova": entry.prova,
        "car_number": entry.car_number,
        "passagem": entry.passagem,
        "team_name": entry.team_name,
    }
