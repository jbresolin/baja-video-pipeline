"""Files on the VPS under BVP_DATA_DIR (docs/api.md §8.2) and the URLs pointing at them.

inbox/<job_id>.<ext>   raws until two holders confirm   (Caddy :8080 /inbox/*)
proxies/<job_id>.mp4   review proxies                   (Caddy :443 /proxies/*)
thumbs/<job_id>.jpg    review thumbnails                (Caddy :443 /thumbs/*)
outbox/<job_id>.mp4    fallback clips until studio ACK  (Caddy :8080 /outbox/*)
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
        self.public_base_url = public_base_url.rstrip("/")
        self.tailnet_base_url = tailnet_base_url.rstrip("/")
        # Tests replace this to simulate a full disk.
        self._free_bytes_reader = free_bytes_reader or (lambda path: shutil.disk_usage(path).free)

    def create_folders(self) -> None:
        for folder in (self.inbox_dir, self.proxies_dir, self.thumbs_dir, self.outbox_dir):
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
    def thumbnail_public_path(job_id: str) -> str:
        return f"/thumbs/{job_id}.jpg"

    # --- Disk use ---

    def inbox_bytes(self) -> int:
        return folder_size_bytes(self.inbox_dir)

    def proxy_bytes(self) -> int:
        return folder_size_bytes(self.proxies_dir) + folder_size_bytes(self.thumbs_dir)

    def outbox_bytes(self) -> int:
        return folder_size_bytes(self.outbox_dir)

    def free_bytes(self) -> int:
        return self._free_bytes_reader(self.data_dir)
