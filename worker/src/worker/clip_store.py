"""Finished clips a worker keeps, and the master's clip archive (design §11 as amended).

Processed clips are the archive. So:

* Every worker keeps the clips it rendered after a verified delivery, as
  clips/<job_id>.<sha256>.mp4. They count against the storage cap and
  are served to the master on :8710 (GET /clip/{job_id}).
* A kept clip may be evicted only once the orchestrator says the master
  has archived that exact clip (by sha256), and only when the worker is
  near its cap, oldest first. Held raws keep their own rules (storage.py).
* The master's archive mirrors the studio box:
      clips/<prova-slug>/<car>/<passagem>.mp4      version 1
      clips/<prova-slug>/<car>/<passagem>.v2.mp4   after a reprocess, ...
  It is never evicted; after the event it is copied to TrueNAS.

The clip being rendered or delivered right now is clips/<job_id>.mp4 (+
its .json sidecar, worker.tasks.process); it is not a kept clip yet.
"""

import logging
import re
from pathlib import Path, PurePosixPath

from shared.layout import is_safe_relative_path
from shared.models import HeldClip

logger = logging.getLogger(__name__)

# "<32 hex job id>.<64 hex sha256>.mp4"
KEPT_CLIP_NAME = re.compile(r"^([0-9a-f]{32})\.([0-9a-f]{64})\.mp4$")
# A worker with less room than this under its cap evicts archived clips
# (the same headroom the orchestrator uses for "near the cap").
EVICTION_HEADROOM_BYTES = 4_000_000_000


class ClipStore:
    def __init__(self, clips_dir: Path) -> None:
        self.clips_dir = clips_dir

    # --- Kept clips ---

    def kept_path(self, job_id: str, sha256: str) -> Path:
        return self.clips_dir / f"{job_id}.{sha256}.mp4"

    def keep(self, rendered_clip: Path, job_id: str, sha256: str) -> Path:
        """Move a delivered clip to its kept name; the same clip twice is kept once."""
        destination = self.kept_path(job_id, sha256)
        rendered_clip.replace(destination)
        logger.info("keeping the delivered clip", extra={"job_id": job_id})
        return destination

    def kept_clips(self) -> list[tuple[Path, HeldClip]]:
        """Every kept clip, oldest first (by modification time)."""
        found = []
        for path in self.clips_dir.iterdir():
            match = KEPT_CLIP_NAME.match(path.name)
            if match and path.is_file():
                found.append((path, HeldClip(job_id=match.group(1), sha256=match.group(2))))
        found.sort(key=lambda item: (item[0].stat().st_mtime, item[0].name))
        return found

    def held_clips(self) -> list[HeldClip]:
        """For heartbeats (HeartbeatRequest.held_clips)."""
        return [clip for _, clip in self.kept_clips()]

    def find(self, job_id: str, sha256: str | None = None) -> Path | None:
        """A kept clip of the job: that exact one, or the newest when sha256 is None.

        Both values come from a URL (GET /clip/{job_id}?sha256=), so anything
        that is not a plain id/hash finds nothing instead of becoming a path.
        """
        if sha256 is not None:
            if not KEPT_CLIP_NAME.match(f"{job_id}.{sha256}.mp4"):
                return None
            path = self.kept_path(job_id, sha256)
            return path if path.is_file() else None
        newest = [path for path, clip in self.kept_clips() if clip.job_id == job_id]
        return newest[-1] if newest else None

    def evict_archived(
        self,
        archived_sha256s: set[str],
        free_bytes_under_cap,
        headroom: int = EVICTION_HEADROOM_BYTES,
    ) -> list[HeldClip]:
        """Near the cap, delete archived kept clips, oldest first, until there is room.

        `free_bytes_under_cap` is a function (Storage.free_bytes_under_cap),
        asked again after each deletion. A clip the master has not archived
        is never deleted, however full the disk.
        """
        evicted = []
        for path, clip in self.kept_clips():
            if free_bytes_under_cap() >= headroom:
                break
            if clip.sha256 not in archived_sha256s:
                continue
            path.unlink(missing_ok=True)
            logger.info("evicted an archived clip to make room", extra={"job_id": clip.job_id})
            evicted.append(clip)
        return evicted

    # --- The master's archive ---

    def archive_path(self, archive_relative_path: str) -> Path:
        """clips/<prova>/<car>/<passagem>[.vN].mp4; refuses paths that leave clips/."""
        if not is_safe_relative_path(archive_relative_path):
            raise ValueError(f"unsafe archive path: {archive_relative_path!r}")
        return self.clips_dir.joinpath(*PurePosixPath(archive_relative_path).parts)

    def archived_clip_count(self) -> int:
        """Clips in the archive tree (the master's "clips archived" on its status page)."""
        return sum(
            1
            for path in self.clips_dir.rglob("*.mp4")
            if path.parent != self.clips_dir and not path.name.startswith(".")
        )
