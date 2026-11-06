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
# Built-in orchestrator_url: a local dev orchestrator. An installed worker
# still has it after enrolling until `--first-run` has joined the tailnet and
# written Caddy's tailnet listener there (installer/baja_installer/first_run.py).
DEFAULT_ORCHESTRATOR_URL = "http://127.0.0.1:8000"
# Shown instead of a raw "connection refused" while that step is pending.
CONNECTION_STEP_PENDING_MESSAGE = (
    "Conexão não concluída: abra 'Baja Worker - concluir conexão' no menu Iniciar."
)


class WorkerConfig(BaseModel):
    """Every setting the daemon reads. See worker/README.md for each field."""

    model_config = ConfigDict(extra="forbid")

    # Caddy's tailnet listener, e.g. "http://100.101.102.103:8080". Every API
    # call except /enroll goes here.
    orchestrator_url: str = DEFAULT_ORCHESTRATOR_URL
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

    # The volunteer clicked "Pausar" (tray icon or status page): finish the
    # current task, take nothing new. Kept here so it survives a reboot.
    paused: bool = False

    # Studio only: the folder OBS plays from (<prova>/<car>/<file>.mp4 plus
    # latest.m3u). Asked on the setup page right after enrollment
    # (worker.studio.clip_folder); None = not chosen yet.
    studio_clip_root: Path | None = None

    # "Usar a rede atual" at "Concluir conexão" (LIVESTACK-41 item 4): this PC
    # stays on the tailnet it was already on, into which the VPS is shared.
    # Sent in every heartbeat; the orchestrator then never makes it a second
    # holder or a peer source, and tells it to deliver through the outbox.
    shared_tailnet: bool = False

    @property
    def is_enrolled(self) -> bool:
        return bool(self.worker_id and self.bearer_token and self.role)

    @property
    def connection_step_pending(self) -> bool:
        """Enrolled, but `--first-run` has not finished the tailnet join yet.

        first_run.py replaces the default orchestrator_url once the PC is on
        the tailnet, so "enrolled and still on the default" means that step
        is still to do (or failed). Volunteers then see
        CONNECTION_STEP_PENDING_MESSAGE instead of a network error.
        """
        return self.is_enrolled and self.orchestrator_url == DEFAULT_ORCHESTRATOR_URL

    def resolved_orchestrator_url(self) -> str:
        """Where the daemon sends API calls.

        While orchestrator_url is still the built-in local default but /enroll
        told us Caddy's tailnet listener (`tailnet_base_url`), use that: it
        is where the worker API lives. Traffic only flows once this PC has
        joined the tailnet (`--first-run`), but then it works even if the
        join step never got to rewrite worker.json.
        """
        if self.orchestrator_url == DEFAULT_ORCHESTRATOR_URL and self.tailnet_base_url:
            return self.tailnet_base_url
        return self.orchestrator_url

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
    """Read the config file; a missing file means "all defaults" (first run).

    Raises on a broken file (JSON syntax, unknown key, wrong type). Code
    that must keep running whatever the file says (the status page, the
    startup sequence) uses worker.config_problems.read_config instead,
    which explains the problem to the volunteer.
    """
    if not config_path.exists():
        return WorkerConfig()
    return WorkerConfig.model_validate_json(read_config_text(config_path))


def read_config_text(config_path: Path) -> str:
    """The file's text. "utf-8-sig" also accepts the byte-order mark that
    Windows Notepad may put at the start of a file it saved."""
    return config_path.read_text(encoding="utf-8-sig")


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


def config_without_identity(config: WorkerConfig) -> WorkerConfig:
    """ "Registrar de novo": forget who this PC is in the Baja fleet, keep the rest.

    Cleared: worker id, bearer token, role, Caddy's tailnet URL and the
    saved tailnet address (all from the old enrollment), and any leftover
    enrollment token. Kept: everything the installer wrote (enroll_url,
    ffmpeg_path, …) and everything the volunteer chose (working folder,
    cap, studio clip folder, paused). raws/ and clips/ are not touched.

    `orchestrator_url` goes back to the built-in default only when the join
    step wrote it (it equals the old tailnet_base_url); a URL someone typed
    in stays. The next enrollment and join write it again.
    """
    updates: dict[str, object] = {
        "worker_id": None,
        "bearer_token": None,
        "role": None,
        "tailnet_base_url": None,
        "tailnet_address": None,
        "enrollment_token": None,
        "shared_tailnet": False,
    }
    if config.tailnet_base_url and config.orchestrator_url == config.tailnet_base_url:
        updates["orchestrator_url"] = DEFAULT_ORCHESTRATOR_URL
    return config.model_copy(update=updates)
