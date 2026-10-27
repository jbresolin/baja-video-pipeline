"""The worker's config file (`worker.json`), read at start and written by setup.

The file sits in the folder the service starts in (the "home" folder; NSSM
sets it as the service's working directory). The working folder for raws,
clips and logs defaults to that same folder but the volunteer may pick
another one on the setup page.

Before enrollment the file holds only what the installer and the setup page
put there (orchestrator URLs, enrollment token, working folder, cap). After
a successful POST /enroll the token is removed and the worker id, bearer
token, role and Caddy's tailnet base URL are stored instead.
"""

import json
import os
import socket
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from shared.models import WorkerRole
from shared.protocol import DEFAULT_STUDIO_RECEIVER_PORT, DEFAULT_WORKER_FILE_SERVER_PORT

CONFIG_FILE_NAME = "worker.json"
DEFAULT_SETUP_PAGE_PORT = 8700
GIGABYTE = 1024**3


class WorkerConfig(BaseModel):
    """Every setting the daemon reads. See worker/README.md for each field."""

    model_config = ConfigDict(extra="forbid")

    # Caddy's tailnet listener, e.g. "http://100.101.102.103:8080". Every API
    # call except /enroll goes here.
    orchestrator_url: str = "http://127.0.0.1:8000"
    # The public site that routes POST /enroll (design §8). None = use
    # orchestrator_url (local dev, where both are the same process).
    enroll_url: str | None = None

    # One-time token from the admin. Removed after a successful enrollment.
    enrollment_token: str | None = None
    # Filled in by enrollment; the three together mean "enrolled".
    worker_id: str | None = None
    bearer_token: str | None = None
    role: WorkerRole | None = None
    # Caddy's tailnet listener from the /enroll answer, e.g.
    # "http://100.64.0.1:8080" (it serves /inbox/* and /outbox/*). The bearer
    # token goes only to this origin and orchestrator_url's (design §17).
    # None in configs enrolled before it existed: then only orchestrator_url.
    tailnet_base_url: str | None = None

    worker_name: str = Field(default_factory=socket.gethostname)
    # Where raws/, clips/, tmp/ and logs/ live. None = the config file's folder.
    working_folder: Path | None = None
    storage_cap_bytes: int = Field(default=100 * GIGABYTE, ge=0)

    ffmpeg_path: str = "ffmpeg"
    # None = "ffprobe" next to ffmpeg_path (the installer bundles both).
    ffprobe_path: str | None = None

    # Our Tailscale IP or MagicDNS name, sent in heartbeats so peers and
    # the studio can be reached. None = ask `tailscale ip -4` at start.
    tailnet_address: str | None = None
    # Address the peer file server and studio receiver listen on. None =
    # the tailnet address (so raws are not exposed on the local LAN), or
    # all interfaces when no tailnet address is known.
    listen_address: str | None = None
    file_server_port: int = DEFAULT_WORKER_FILE_SERVER_PORT
    receiver_port: int = DEFAULT_STUDIO_RECEIVER_PORT
    setup_page_port: int = DEFAULT_SETUP_PAGE_PORT

    # Studio only: the folder OBS plays from (<prova>/<car>/<passagem>.mp4
    # plus latest.m3u). None = "<working folder>/box".
    studio_clip_root: Path | None = None

    @property
    def is_enrolled(self) -> bool:
        return bool(self.worker_id and self.bearer_token and self.role)

    def resolved_enroll_url(self) -> str:
        return self.enroll_url or self.orchestrator_url

    def resolved_working_folder(self, config_path: Path) -> Path:
        return self.working_folder or config_path.parent

    def resolved_ffprobe_path(self) -> str:
        """ffprobe beside ffmpeg: "C:/x/ffmpeg.exe" → "C:/x/ffprobe.exe"."""
        if self.ffprobe_path:
            return self.ffprobe_path
        ffmpeg = Path(self.ffmpeg_path)
        probe_name = ffmpeg.name.replace("ffmpeg", "ffprobe")
        if ffmpeg.parent == Path("."):
            return probe_name  # a bare name: let the system PATH find it
        return str(ffmpeg.with_name(probe_name))

    def resolved_studio_clip_root(self, config_path: Path) -> Path:
        return self.studio_clip_root or self.resolved_working_folder(config_path) / "box"


def load_config(config_path: Path) -> WorkerConfig:
    """Read the config file; a missing file means "all defaults" (first run)."""
    if not config_path.exists():
        return WorkerConfig()
    return WorkerConfig.model_validate_json(config_path.read_text(encoding="utf-8"))


def save_config(config_path: Path, config: WorkerConfig) -> None:
    """Write the config so that a crash never leaves a half-written file."""
    text = json.dumps(config.model_dump(mode="json"), indent=2) + "\n"
    write_text_atomically(config_path, text)


def write_text_atomically(path: Path, text: str) -> None:
    """Write to a hidden temporary file, then rename it over `path`.

    A rename within one folder is atomic on Windows and Linux, so readers
    (OBS, the installer, the next start of the daemon) see either the old
    file or the new one, never half of it.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.part")
    with temporary.open("w", encoding="utf-8", newline="\n") as file:
        file.write(text)
        file.flush()
        os.fsync(file.fileno())
    os.replace(temporary, path)
