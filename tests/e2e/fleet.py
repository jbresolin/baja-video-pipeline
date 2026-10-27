"""Builds the whole system inside one test process, with no sockets and no ffmpeg.

    system = build_system(tmp_path)
    master = system.start_worker(WorkerRole.MASTER, "master.test")
    ...
    system.stop()

What is real and what is fake:
  * real: the orchestrator app (orchestrator.app.create_app), the review UI
    app (review_ui.app.create_app) and the worker daemons
    (worker.daemon.Daemon, all three roles), with their own HTTP clients;
  * fake: the network (one in-memory RoutingTransport that sends each
    request to the app registered for its host:port), Caddy
    (FakeCaddyTransport), the WhatsApp CDN, Evolution, Tailscale and the
    Baja app (the orchestrator harness fakes), and ffmpeg/ffprobe
    (FakeMediaTools: writes placeholder files instead of rendering).

Addresses on the fake network:
    http://orchestrator.test       orchestrator API (worker orchestrator_url)
    http://100.64.0.1:8080         "Caddy" tailnet listener (BVP_TAILNET_BASE_URL)
    http://<name>.test:8710        each master/worker's peer file server
    http://studio.test:8720        the studio receiver
"""

import base64
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from orchestrator.app import create_app as create_orchestrator_app
from orchestrator.testing.harness import (
    REVIEWER_NAME,
    REVIEWER_PASSWORD,
    TAILNET_BASE_URL,
    OrchestratorHarness,
    build_harness,
)
from review_ui.app import create_app as create_review_ui_app
from review_ui.settings import ReviewUiSettings
from shared.models import WorkerCapabilities, WorkerRole
from shared.protocol import DEFAULT_STUDIO_RECEIVER_PORT, DEFAULT_WORKER_FILE_SERVER_PORT
from shared.testing.asgi_transport import SyncAsgiTransport
from tests.e2e.fake_caddy import FakeCaddyTransport
from tests.worker.fakes import FakeMediaTools, RoutingTransport
from worker.config import WorkerConfig
from worker.daemon import Daemon, DaemonTimings, build_api_client
from worker.enrollment import enroll
from worker.status import StatusBoard
from worker.tasks.environment import TaskSettings

ORCHESTRATOR_URL = "http://orchestrator.test"
# Starts with the JPEG magic number (FF D8 FF), which is all the orchestrator checks.
TINY_JPEG = b"\xff\xd8\xff\xe0" + b"tiny thumbnail"

# Real waits are 15 s heartbeats and 5 s between /next rounds; here
# everything happens within a fraction of a second.
FAST_TIMINGS = DaemonTimings(
    heartbeat_interval_seconds=0.1,
    max_dispatch_wait_seconds=0.05,
    ack_retry_delay_seconds=0.05,
    task_settings=TaskSettings(
        push_ack_wait_seconds=10,
        report_retry_delay_seconds=0,
        transfer_retry_delay_seconds=0,
    ),
)


class CopyingMediaTools(FakeMediaTools):
    """The worker tests' stub ffmpeg, made realistic enough for the real orchestrator.

    A "render" copies its input file to the output (so proxies and clips are
    real bytes with a real hash), and a thumbnail is a tiny file starting
    with the JPEG magic number, which is what the orchestrator checks.
    Probing still answers the stub's fixed 12 s, 1080p, 29.97 fps.
    """

    def run_ffmpeg(self, args, cancel, *, what):
        super().run_ffmpeg(args, cancel, what=what)
        output = Path(args[-1])
        if output.suffix.lower() in (".jpg", ".jpeg"):
            output.write_bytes(TINY_JPEG)
        else:
            output.write_bytes(Path(args[args.index("-i") + 1]).read_bytes())


def wait_until(condition: Callable[[], object], timeout: float = 30, what: str = "") -> object:
    """Poll `condition` until it returns something truthy; fail after `timeout` seconds."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = condition()
        if result:
            return result
        time.sleep(0.05)
    raise AssertionError(f"timed out after {timeout} s waiting for {what or condition}")


@dataclass
class System:
    harness: OrchestratorHarness
    network: RoutingTransport
    caddy: FakeCaddyTransport
    # A browser-like client for the review UI, signed in as the reviewer.
    review_ui: httpx.Client
    root: Path
    daemons: list[Daemon] = field(default_factory=list)

    def start_worker(self, role: WorkerRole, host: str) -> Daemon:
        """Enroll a worker of `role` through the real /enroll and start its daemon.

        `host` is its tailnet address on the fake network; its peer file
        server (or studio receiver) is mounted there at the contract port.
        """
        token = self.harness.client.post(
            "/admin/enrollment-tokens",
            json={"volunteer_name": host, "role": role},
            headers=self.harness.admin_headers,
        ).json()["token"]
        folder = self.root / host
        config_path = folder / "worker.json"
        folder.mkdir(parents=True)
        config = WorkerConfig(
            orchestrator_url=ORCHESTRATOR_URL,
            enrollment_token=token,
            worker_name=host,
            working_folder=folder,
            tailnet_address=host,
            storage_cap_bytes=500_000_000_000,
            studio_clip_root=folder / "box" if role == WorkerRole.STUDIO else None,
        )
        config = enroll(
            config,
            config_path,
            build_api_client(config, self.network),
            WorkerCapabilities(has_nvenc=False, video_encoder="libx264"),
        )
        daemon = Daemon(
            config,
            config_path,
            StatusBoard(),
            threading.Event(),
            media=CopyingMediaTools(),
            transport=self.network,
            start_servers=False,
            timings=FAST_TIMINGS,
        )
        port = (
            DEFAULT_STUDIO_RECEIVER_PORT
            if role == WorkerRole.STUDIO
            else DEFAULT_WORKER_FILE_SERVER_PORT
        )
        self.network.add_app(f"http://{host}:{port}", daemon.http_app())
        daemon.start()
        self.daemons.append(daemon)
        return daemon

    def stop(self) -> None:
        for daemon in self.daemons:
            daemon.stop()
        self.review_ui.close()
        self.harness.client.close()
        self.harness.context.database.close()


def build_system(root: Path) -> System:
    # The orchestrator, wired to fake WhatsApp CDN/Evolution/Tailscale/Baja
    # app by the test harness (its tailnet base URL is the "Caddy" below).
    harness = build_harness(root / "vps")
    orchestrator = SyncAsgiTransport(
        create_orchestrator_app(harness.context, run_background_tasks=False)
    )
    network = RoutingTransport()
    network.add_transport(ORCHESTRATOR_URL, orchestrator)
    caddy = FakeCaddyTransport(orchestrator, harness.context.settings.data_dir)
    network.add_transport(TAILNET_BASE_URL, caddy)

    # The review UI talks to the same orchestrator over HTTP, as a reviewer.
    ui_app = create_review_ui_app(
        ReviewUiSettings(orchestrator_url=ORCHESTRATOR_URL, public_base_url=""),
        orchestrator_http_client=httpx.Client(transport=orchestrator, base_url=ORCHESTRATOR_URL),
    )
    credentials = base64.b64encode(f"{REVIEWER_NAME}:{REVIEWER_PASSWORD}".encode()).decode()
    review_ui = httpx.Client(
        transport=SyncAsgiTransport(ui_app),
        base_url="http://review.test",
        headers={"Authorization": f"Basic {credentials}"},
    )
    return System(harness, network, caddy, review_ui, root)
