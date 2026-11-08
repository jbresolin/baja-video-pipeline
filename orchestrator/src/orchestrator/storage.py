"""Files on the VPS under BVP_DATA_DIR (docs/api.md §8.2) and the URLs pointing at them.

inbox/<job_id>.<ext>   raws until two holders confirm   (Caddy :8080 /inbox/*)
proxies/<job_id>.mp4   review proxies                   (Caddy :443 /proxies/*)
thumbs/<job_id>.jpg    review thumbnails                (Caddy :443 /thumbs/*)
outbox/<job_id>.mp4    fallback clips until studio ACK  (Caddy :8080 /outbox/*)
originals/<job_id>.<ext>  raws a reviewer asked for     (Caddy :443 /original-downloads/*)
previews/<job_id>.<clip sha256>.mp4  480p clip previews (Caddy :443 /previews/*)
requested_clips/<job_id>.mp4  clips a reviewer asked for (orchestrator /requested-clips/*)
db/bvp.sqlite3         the jobs DB
"""

import re
import shutil
from collections.abc import Callable
from pathlib import Path, PurePosixPath

from shared.http_transfer import partial_path_for

# Extensions we keep as they are; anything else is stored as ".bin".
RAW_EXTENSION_PATTERN = re.compile(r"^\.[a-z0-9]{1,5}$")
# For WhatsApp *video* messages, which carry no file name.
EXTENSION_BY_MIMETYPE = {"video/mp4": ".mp4", "video/quicktime": ".mov"}


def raw_extension_for(original_filename: str | None, mimetype: str | None) -> str:
    """Inbox file extension: the original one lowercased, else from the mimetype, else .bin."""
    if original_filename:
        suffix = PurePosixPath(original_filename.replace("\\", "/")).suffix.lower()
        if RAW_EXTENSION_PATTERN.match(suffix):
            return suffix
    return EXTENSION_BY_MIMETYPE.get((mimetype or "").lower(), ".bin")


def folder_size_bytes(folder: Path) -> int:
    """Total size of the files directly in `folder`, partial dot-files included."""
    if not folder.exists():
        return 0
    return sum(entry.stat().st_size for entry in folder.iterdir() if entry.is_file())


def delete_file_and_partials(path: Path) -> bool:
    """Delete a file and any half-written dot-files beside it. True if the file existed."""
    existed = path.exists()
    path.unlink(missing_ok=True)
    partial = partial_path_for(path)
    partial.unlink(missing_ok=True)
    path.with_name(f"{partial.name}.json").unlink(missing_ok=True)
    return existed


class VpsLayout:
    def __init__(
        self,
        data_dir: Path,
        public_base_url: str,
        tailnet_base_url: str,
        free_bytes_reader: Callable[[Path], int] | None = None,
    ) -> None:
        self.data_dir = data_dir
        self.inbox_dir = data_dir / "inbox"
        self.proxies_dir = data_dir / "proxies"
        self.thumbs_dir = data_dir / "thumbs"
        self.outbox_dir = data_dir / "outbox"
        # "Baixar original" (LIVESTACK-41 item 15): temporary, never a holder.
        self.originals_dir = data_dir / "originals"
        # "Ver clipe" and "Baixar clipe" (LIVESTACK-52): temporary, never a source.
        self.previews_dir = data_dir / "previews"
        self.requested_clips_dir = data_dir / "requested_clips"
        self.public_base_url = public_base_url.rstrip("/")
        self.tailnet_base_url = tailnet_base_url.rstrip("/")
        # Tests replace this to simulate a full disk.
        self._free_bytes_reader = free_bytes_reader or (lambda path: shutil.disk_usage(path).free)

    def create_folders(self) -> None:
        folders = (
            self.inbox_dir,
            self.proxies_dir,
            self.thumbs_dir,
            self.outbox_dir,
            self.originals_dir,
            self.previews_dir,
            self.requested_clips_dir,
        )
        for folder in folders:
            folder.mkdir(parents=True, exist_ok=True)

    # --- Paths ---

    def inbox_path(self, job_id: str, raw_extension: str) -> Path:
        return self.inbox_dir / f"{job_id}{raw_extension}"

    def proxy_path(self, job_id: str) -> Path:
        return self.proxies_dir / f"{job_id}.mp4"

    def thumbnail_path(self, job_id: str) -> Path:
        return self.thumbs_dir / f"{job_id}.jpg"

    def outbox_path(self, job_id: str) -> Path:
        return self.outbox_dir / f"{job_id}.mp4"

    def original_path(self, job_id: str, raw_extension: str) -> Path:
        return self.originals_dir / f"{job_id}{raw_extension}"

    def preview_path(self, job_id: str, clip_sha256: str) -> Path:
        """One file per clip version, so a newer one never overwrites the one playing."""
        return self.previews_dir / f"{job_id}.{clip_sha256}.mp4"

    def preview_paths_of_job(self, job_id: str) -> list[Path]:
        """Every complete preview file of the job (normally at most one)."""
        if not self.previews_dir.exists():
            return []
        return sorted(
            path
            for path in self.previews_dir.glob(f"{job_id}.*.mp4")
            if not path.name.startswith(".")
        )

    def requested_clip_path(self, job_id: str) -> Path:
        return self.requested_clips_dir / f"{job_id}.mp4"

    # --- URLs ---

    def inbox_download_url(self, job_id: str, raw_extension: str) -> str:
        return f"{self.tailnet_base_url}/inbox/{job_id}{raw_extension}"

    def outbox_download_url(self, job_id: str) -> str:
        return f"{self.tailnet_base_url}/outbox/{job_id}.mp4"

    def api_url(self, path: str) -> str:
        """An orchestrator endpoint as workers reach it (through Caddy's tailnet listener)."""
        return f"{self.tailnet_base_url}{path}"

    @staticmethod
    def proxy_public_path(job_id: str) -> str:
        """Caddy path relative to BVP_PUBLIC_BASE_URL (docs/api.md §8.2)."""
        return f"/proxies/{job_id}.mp4"

    @staticmethod
    def clip_download_public_path(job_id: str, file_name: str | None = None) -> str:
        """The outbox clip for reviewers: Caddy :443 /clip-downloads/* (reviewer auth).

        With `file_name` (item 12): "/clip-downloads/<job_id>/enduro-042-p3.mp4".
        Caddy serves outbox/<job_id>.mp4 for it and names the download after
        the last part of the URL, so the saved file says what it is.
        """
        if file_name:
            return f"/clip-downloads/{job_id}/{file_name}"
        return f"/clip-downloads/{job_id}.mp4"

    @staticmethod
    def original_download_public_path(job_id: str, file_name: str) -> str:
        """An original on the VPS for reviewers (item 15), named for the download:
        "/original-downloads/<job_id>/<readable raw name>" → originals/<job_id>.<ext>."""
        return f"/original-downloads/{job_id}/{file_name}"

    @staticmethod
    def preview_public_path(job_id: str, clip_sha256: str) -> str:
        """A clip preview for signed-in browsers (LIVESTACK-52), Caddy :443 /previews/*."""
        return f"/previews/{job_id}.{clip_sha256}.mp4"

    @staticmethod
    def requested_clip_public_path(job_id: str, file_name: str) -> str:
        """A clip a reviewer asked for (LIVESTACK-52), named for the download:
        "/requested-clips/<job_id>/suspensao-007-p2.mp4" → requested_clips/<job_id>.mp4.
        Served by the orchestrator itself (Caddy passes the path on), which
        is how it knows when a download finished (domain/requested_clips.py)."""
        return f"/requested-clips/{job_id}/{file_name}"

    @staticmethod
    def thumbnail_public_path(job_id: str) -> str:
        return f"/thumbs/{job_id}.jpg"

    # --- Disk use ---

    def inbox_bytes(self) -> int:
        return folder_size_bytes(self.inbox_dir)

    def proxy_bytes(self) -> int:
        return folder_size_bytes(self.proxies_dir) + folder_size_bytes(self.thumbs_dir)

    def outbox_bytes(self) -> int:
        return folder_size_bytes(self.outbox_dir)

    def originals_bytes(self) -> int:
        return folder_size_bytes(self.originals_dir)

    def previews_bytes(self) -> int:
        return folder_size_bytes(self.previews_dir)

    def requested_clips_bytes(self) -> int:
        return folder_size_bytes(self.requested_clips_dir)

    def free_bytes(self) -> int:
        return self._free_bytes_reader(self.data_dir)
