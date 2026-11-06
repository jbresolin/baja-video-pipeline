"""The startup sequence after the status page is up (worker.lifecycle).

LIVESTACK-41 items 1, 2, 3 and 5, seen from the volunteer's side:
  * item 1: while the saved tailnet address is not on this PC, the page is
    up and says "Esta máquina não está conectada à rede Baja." (with what
    fixes it); the daemon starts as soon as the address appears;
  * item 2: "Registrar de novo" forgets only the identity, keeps every
    other setting and the videos, and the page goes back to the token form;
  * item 3: a broken worker.json is waited out, not crashed on;
  * item 5: a studio is asked for its clip folder before it starts.

Each test runs WorkerLifecycle.run() in a background thread, exactly as
worker.__main__ does, but with fakes: the "daemon" is an object that only
remembers it was started and stopped, Tailscale is a function the test
controls, and /enroll goes to the in-memory fake orchestrator.
"""

import threading
import time

import httpx
import pytest

from shared.models import WorkerCapabilities, WorkerRole
from shared.testing.asgi_transport import SyncAsgiTransport
from shared.testing.credentials import PLACEHOLDER_WORKER_TOKEN
from tests.worker.fake_orchestrator import BASE_URL, GOOD_ENROLLMENT_TOKEN, FakeOrchestrator
from tests.worker.fakes import RoutingTransport
from worker.api_client import OrchestratorClient
from worker.config import WorkerConfig, load_config, save_config
from worker.enrollment import TAILSCALE_AUTH_KEY_FILE_NAME
from worker.lifecycle import WorkerLifecycle
from worker.pause import PauseSwitch
from worker.setup_page import build_setup_app
from worker.status import Phase, StatusBoard, TailnetProblem, TailnetProblemKind
from worker.tray.state import Colour, ServiceState, view_for

PORT = 8700
SAVED_ADDRESS = "100.64.0.9"
FORM = {"Content-Type": "application/x-www-form-urlencoded"}
JSON = {"Content-Type": "application/json"}


class FakeDaemon:
    """Stands in for worker.daemon.Daemon: remembers its config and its stop."""

    def __init__(self, config: WorkerConfig, stop_event: threading.Event) -> None:
        self.config = config
        self.stop_event = stop_event
        self.stopped = False

    def stop(self) -> None:
        self.stopped = True


class Harness:
    """A lifecycle in a thread, its page, and switches for the fakes."""

    def __init__(self, tmp_path, role: WorkerRole = WorkerRole.WORKER) -> None:
        self.home = tmp_path / "home"
        self.config_path = self.home / "worker.json"
        self.status = StatusBoard()
        self.shutdown = threading.Event()
        self.submitted = threading.Event()
        self.daemons: list[FakeDaemon] = []
        # What "Tailscale" answers for an address: None = it is on this PC.
        self.tailnet_problem: TailnetProblem | None = None
        self.prepared_folders: list = []
        self.orchestrator = FakeOrchestrator(tmp_path / "vps", role=role)
        network = RoutingTransport()
        network.add_app(BASE_URL, self.orchestrator.app)
        self.lifecycle = WorkerLifecycle(
            self.config_path,
            self.status,
            self.shutdown,
            setup_submitted=self.submitted,
            start_daemon=self._start_daemon,
            enroll_api_for=lambda config: OrchestratorClient(
                httpx.Client(transport=network), BASE_URL
            ),
            capabilities_for=lambda config: WorkerCapabilities(
                has_nvenc=False, video_encoder="libx264"
            ),
            check_tailnet=lambda address: self.tailnet_problem,
            prepare_studio_folder=self._prepare_folder,
            config_poll_seconds=0.02,
            tailnet_poll_seconds=0.02,
        )
        app = build_setup_app(
            self.config_path,
            self.status,
            self.submitted,
            PORT,
            pause=PauseSwitch(self.config_path),
            re_register=self.lifecycle.re_register,
        )
        self.page = httpx.Client(
            transport=SyncAsgiTransport(app), base_url=f"http://127.0.0.1:{PORT}"
        )
        self.thread = threading.Thread(target=self.lifecycle.run, daemon=True)

    def _start_daemon(self, config, stop):
        daemon = FakeDaemon(config, stop)
        self.daemons.append(daemon)
        return daemon

    def _prepare_folder(self, folder):
        self.prepared_folders.append(folder)
        return None

    def write_config(self, **settings) -> None:
        save_config(self.config_path, WorkerConfig(**settings))

    def start(self) -> "Harness":
        self.thread.start()
        return self

    def stop(self) -> None:
        self.shutdown.set()
        if self.thread.is_alive():
            self.thread.join(5)

    def phase(self) -> Phase:
        return self.status.facts().phase

    def tray(self):
        return view_for(self.page.get("/api/status").json(), ServiceState.RUNNING, was_running=True)


def wait_until(condition, timeout=5.0):
    """Poll `condition` until it is true; fail the test after `timeout` seconds."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(0.01)
    raise AssertionError("condition never became true")


def enrolled_worker(**overrides) -> dict:
    """An enrolled worker.json with installer and volunteer settings in it."""
    settings = dict(
        orchestrator_url="http://100.64.0.1:8080",
        tailnet_base_url="http://100.64.0.1:8080",
        enroll_url="https://baja.example",
        ffmpeg_path="C:/Program Files/BajaWorker/ffmpeg/ffmpeg.exe",
        worker_id="w-old",
        bearer_token=PLACEHOLDER_WORKER_TOKEN,
        role=WorkerRole.WORKER,
        tailnet_address=SAVED_ADDRESS,
        storage_cap_bytes=50 * 1024**3,
    )
    settings.update(overrides)
    return settings


@pytest.fixture
def harness(tmp_path):
    created = []

    def make(role=WorkerRole.WORKER) -> Harness:
        created.append(Harness(tmp_path, role))
        return created[-1]

    yield make
    for each in created:
        each.stop()


# --- item 1: the page is up while the tailnet is missing ---------------------


def test_waiting_for_the_tailnet_is_a_visible_state_and_nothing_starts(harness):
    h = harness()
    h.write_config(**enrolled_worker())
    # Tailscale is logged out: e.g. the PC was removed from the Baja tailnet.
    h.tailnet_problem = TailnetProblem(TailnetProblemKind.NOT_CONNECTED, SAVED_ADDRESS)
    h.start()

    wait_until(lambda: h.phase() == Phase.WAITING_FOR_TAILNET)

    # The page answers (before: the service waited before even starting it).
    html = h.page.get("/").text
    assert "Esta máquina não está conectada à rede Baja." in html
    # ...with what fixes it, right there.
    assert "Concluir conexão" in html and "Registrar de novo" in html
    # The tray is yellow and offers both fixes in its menu.
    view = h.tray()
    assert view.colour == Colour.YELLOW
    assert view.menu.finish_connection and view.menu.re_register
    # And nothing that needs the tailnet was started.
    assert h.daemons == []


def test_the_daemon_starts_as_soon_as_the_address_appears(harness):
    h = harness()
    h.write_config(**enrolled_worker())
    h.tailnet_problem = TailnetProblem(TailnetProblemKind.TAILSCALE_NOT_ANSWERING, SAVED_ADDRESS)
    h.start()
    wait_until(lambda: h.phase() == Phase.WAITING_FOR_TAILNET)

    h.tailnet_problem = None  # Tailscale finished starting after a reboot

    wait_until(lambda: len(h.daemons) == 1)
    wait_until(lambda: h.phase() == Phase.RUNNING)
    assert h.page.get("/api/status").json()["tailnet_problem"] is None


def test_an_address_that_belongs_to_another_pc_is_said_explicitly(harness):
    h = harness()
    h.write_config(**enrolled_worker())
    # Tailscale runs, but with another address: this PC is on another tailnet.
    h.tailnet_problem = TailnetProblem(
        TailnetProblemKind.ADDRESS_NOT_ON_THIS_PC, SAVED_ADDRESS, "100.70.1.2"
    )
    h.start()
    wait_until(lambda: h.phase() == Phase.WAITING_FOR_TAILNET)

    html = h.page.get("/").text

    # Not a vague "waiting": it names both addresses and says why.
    assert f"O endereço salvo ({SAVED_ADDRESS}) não é mais deste PC" in html
    assert "100.70.1.2" in html


def test_a_new_address_written_while_waiting_is_picked_up(harness):
    # "Concluir conexão" (or "Usar a rede atual") rewrites tailnet_address
    # while the worker waits; the next round must read the file again.
    h = harness()
    h.write_config(**enrolled_worker())
    checked = []

    def tailscale(address):
        checked.append(address)
        return (
            None
            if address == "100.64.0.42"
            else TailnetProblem(TailnetProblemKind.NOT_CONNECTED, address)
        )

    h.lifecycle._check_tailnet = tailscale
    h.start()
    wait_until(lambda: h.phase() == Phase.WAITING_FOR_TAILNET)

    h.write_config(**enrolled_worker(tailnet_address="100.64.0.42"))

    wait_until(lambda: len(h.daemons) == 1)
    assert h.daemons[0].config.tailnet_address == "100.64.0.42"


# --- item 3: a broken worker.json is waited out ------------------------------


def test_a_broken_file_is_shown_until_it_is_fixed_then_the_worker_goes_on(harness):
    h = harness()
    h.config_path.parent.mkdir(parents=True)
    h.config_path.write_text('{"storage_cap_bytes": 1,}', encoding="utf-8")
    h.start()

    wait_until(lambda: h.phase() == Phase.CONFIG_PROBLEM)
    assert "linha 1" in h.page.get("/").text
    assert h.thread.is_alive()  # waiting, not crashed

    # The volunteer removes the comma and saves.
    h.config_path.write_text('{"storage_cap_bytes": 1}', encoding="utf-8")

    # Not enrolled yet, so the next step is the token form.
    wait_until(lambda: h.phase() == Phase.SETUP)
    assert "name='token'" in h.page.get("/").text


# --- item 2: "Registrar de novo" ----------------------------------------------


def test_re_registering_forgets_only_the_identity_and_shows_the_token_form(harness):
    h = harness()
    h.write_config(**enrolled_worker(working_folder=str(h.home / "data"), paused=True))
    # Videos on disk and an old Tailscale key file that must not survive.
    raw = h.home / "data" / "raws" / "joao-20261104-232909-2f2510a3.mp4"
    raw.parent.mkdir(parents=True)
    raw.write_bytes(b"raw video")
    (h.home / TAILSCALE_AUTH_KEY_FILE_NAME).write_text("tskey-old\n")
    h.start()
    wait_until(lambda: len(h.daemons) == 1)

    answer = h.page.post("/api/re-register", content="{}", headers=JSON)

    assert answer.status_code == 200
    # The running daemon was stopped, and the page asks for a token again.
    wait_until(lambda: h.daemons[0].stopped)
    wait_until(lambda: h.phase() == Phase.SETUP)
    assert "name='token'" in h.page.get("/").text
    config = load_config(h.config_path)
    # The identity is gone...
    assert config.worker_id is None and config.bearer_token is None and config.role is None
    assert config.tailnet_base_url is None and config.tailnet_address is None
    # ...everything the installer and the volunteer set is still there...
    assert config.enroll_url == "https://baja.example"
    assert config.ffmpeg_path == "C:/Program Files/BajaWorker/ffmpeg/ffmpeg.exe"
    assert config.storage_cap_bytes == 50 * 1024**3
    assert config.working_folder == h.home / "data"
    assert config.paused is True
    # ...and so are the videos. The spent Tailscale key file is gone.
    assert raw.read_bytes() == b"raw video"
    assert not (h.home / TAILSCALE_AUTH_KEY_FILE_NAME).exists()


def test_after_re_registering_a_new_token_enrolls_and_starts_again(harness):
    h = harness()
    h.write_config(
        **enrolled_worker(orchestrator_url=BASE_URL, enroll_url=BASE_URL, tailnet_base_url=None)
    )
    h.start()
    wait_until(lambda: len(h.daemons) == 1)
    h.page.post("/api/re-register", content="{}", headers=JSON)
    wait_until(lambda: h.phase() == Phase.SETUP)

    # The volunteer pastes the new token into the form.
    folder = h.home / "data"
    h.page.post(
        "/setup", content=f"token={GOOD_ENROLLMENT_TOKEN}&folder={folder}&cap_gb=50", headers=FORM
    )

    wait_until(lambda: len(h.daemons) == 2)
    assert h.daemons[1].config.worker_id == "worker-1"  # the fake orchestrator's new id


def test_re_register_is_a_post_with_json_only(harness):
    # Like Pausar/Desligar: a web site in the volunteer's browser cannot
    # press it (no JSON from a plain form, and a foreign Origin is refused).
    h = harness()
    h.write_config(**enrolled_worker())
    assert h.page.post("/api/re-register", content="x=1", headers=FORM).status_code == 403
    evil = {**JSON, "Origin": "https://evil.example"}
    assert h.page.post("/api/re-register", content="{}", headers=evil).status_code == 403
    assert load_config(h.config_path).worker_id == "w-old"


def test_a_revoked_worker_is_told_and_offered_registrar_de_novo(harness):
    # The heartbeat got 401 (an admin revoked this worker): the heartbeat
    # code sets identity_rejected (tests/worker/test_heartbeat.py).
    h = harness()
    h.write_config(**enrolled_worker())
    h.start()
    wait_until(lambda: h.phase() == Phase.RUNNING)

    h.status.set_identity_rejected(True)

    html = h.page.get("/").text
    assert "removido da equipe Baja" in html and "Registrar de novo" in html
    view = h.tray()
    assert view.colour == Colour.RED and view.menu.re_register


# --- item 5: the studio's clip folder ------------------------------------------


def test_a_studio_is_asked_for_its_clip_folder_before_it_starts(harness, tmp_path):
    h = harness(role=WorkerRole.STUDIO)
    h.write_config(**enrolled_worker(role=WorkerRole.STUDIO))
    h.start()

    wait_until(lambda: h.phase() == Phase.STUDIO_FOLDER)
    html = h.page.get("/").text
    assert "name='clip_folder'" in html
    assert h.daemons == []
    assert h.tray().headline == "falta escolher a pasta dos clipes"

    # A relative path is refused with a pt-BR message...
    h.page.post("/setup/studio-folder", content="clip_folder=BajaClips", headers=FORM)
    assert "caminho completo" in h.page.get("/").text
    # ...a full path is saved, prepared (created, opened to OBS) and the studio starts.
    clips = tmp_path / "BajaClips"
    h.page.post("/setup/studio-folder", content=f"clip_folder={clips}", headers=FORM)

    wait_until(lambda: len(h.daemons) == 1)
    assert load_config(h.config_path).studio_clip_root == clips
    assert h.prepared_folders == [clips]


def test_the_studio_status_page_shows_the_folder_and_the_playlist_for_obs(harness, tmp_path):
    h = harness(role=WorkerRole.STUDIO)
    clips = tmp_path / "BajaClips"
    h.write_config(**enrolled_worker(role=WorkerRole.STUDIO, studio_clip_root=str(clips)))
    h.start()
    wait_until(lambda: h.phase() == Phase.RUNNING)

    html = h.page.get("/").text

    playlist = str(clips / "latest.m3u")
    assert str(clips) in html and playlist in html
    # A copy button carrying the full path, and the one-line OBS hint.
    assert f"data-copy='{playlist}'" in html
    assert "Fonte de vídeo VLC" in html


def test_a_daemon_that_stops_itself_is_started_again_from_worker_json(harness):
    # The daemon may ask for a fresh start by setting its own stop event,
    # e.g. after the orchestrator changed its role and it rewrote worker.json
    # (LIVESTACK-42 item 8). The process and the page stay up; a new daemon
    # starts with what the file says now.
    h = harness()
    h.write_config(**enrolled_worker())
    h.start()
    wait_until(lambda: len(h.daemons) == 1)

    h.write_config(**enrolled_worker(role=WorkerRole.MASTER))
    h.daemons[0].stop_event.set()

    wait_until(lambda: len(h.daemons) == 2)
    assert h.daemons[0].stopped
    assert h.daemons[1].config.role == WorkerRole.MASTER
