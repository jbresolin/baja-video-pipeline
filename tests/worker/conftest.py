"""Fixtures for the worker tests.

pytest runs this file before the tests in this folder; every function
marked @pytest.fixture can be requested by a test just by naming it as a
parameter.

The main one is `harness`: a worker whose parts are all real (dispatcher,
stage runners, heartbeat, storage, task handlers) except for three things:
  * the orchestrator is FakeOrchestrator (in memory, records requests);
  * the "network" is RoutingTransport (in-process apps, no sockets);
  * ffmpeg/ffprobe are FakeMediaTools (no real video work).
Tests call `harness.claim_and_run(task)` to push one task through the
same path the daemon uses: /next → /claim → stage runner → handler →
report. Everything runs on the test's own thread, so there is no timing
to wait for.
"""

from dataclasses import dataclass
from pathlib import Path
from threading import Event

import httpx
import pytest

from shared.models import Peer, WorkerRole
from tests.worker.fake_orchestrator import BASE_URL, BEARER_TOKEN, WORKER_ID, FakeOrchestrator
from tests.worker.fakes import FakeMediaTools, RoutingTransport
from worker.api_client import OrchestratorClient
from worker.dispatcher import STAGE_KINDS_BY_ROLE, Dispatcher
from worker.heartbeat import Heartbeater
from worker.leases import LeaseTracker
from worker.servers import build_peer_file_server_app
from worker.stages import StageRunner
from worker.status import StatusBoard
from worker.storage import Storage
from worker.studio.acknowledger import DeliveryAcknowledger
from worker.studio.library import StudioLibrary
from worker.studio.receiver_app import build_receiver_app
from worker.tasks import TASK_HANDLERS
from worker.tasks.environment import MeasurementLog, TaskEnvironment, TaskSettings

GB = 1024**3
PEER_URL = "http://peer.test:8710"
STUDIO_URL = "http://studio.test:8720"

# Fast settings so tests never sit waiting: no pause between report
# retries, and only 0.3 s (instead of 60 s) for the studio's ACK.
FAST_SETTINGS = TaskSettings(
    push_ack_wait_seconds=0.3, report_attempts=3, report_retry_delay_seconds=0
)


def make_api_client(network: httpx.BaseTransport) -> OrchestratorClient:
    return OrchestratorClient(
        httpx.Client(transport=network), BASE_URL, worker_id=WORKER_ID, bearer_token=BEARER_TOKEN
    )


@dataclass
class WorkerHarness:
    orchestrator: FakeOrchestrator
    network: RoutingTransport
    media: FakeMediaTools
    storage: Storage
    env: TaskEnvironment
    leases: LeaseTracker
    shutdown: Event
    status: StatusBoard
    heartbeater: Heartbeater
    stages: dict[str, StageRunner]
    dispatcher: Dispatcher
    tmp_path: Path

    def claim_and_run(self, task) -> None:
        """Offer `task`, let the dispatcher claim it, run it to the end."""
        self.orchestrator.offer(task)
        self.dispatcher.run_once()
        stage = self.stages[task.kind]
        assert not stage.is_idle, "the dispatcher did not claim the offered task"
        stage.run_submitted_now()

    def add_peer_holding(self, job_id: str, raw_bytes: bytes) -> Storage:
        """A second worker at peer.test:8710 that holds this raw and serves it."""
        peer_storage = Storage(self.tmp_path / "peer", 100 * GB, disk_free=lambda _: 500 * GB)
        peer_storage.raw_path(job_id, ".mp4").write_bytes(raw_bytes)
        self.network.add_app(PEER_URL, build_peer_file_server_app(peer_storage, "peer-1", "worker"))
        self.orchestrator.peers.append(
            Peer(
                worker_id="peer-1",
                name="peer",
                role=WorkerRole.WORKER,
                tailnet_address="peer.test",
                file_server_port=8710,
                receiver_port=None,
                online=True,
                last_heartbeat=None,
            )
        )
        return peer_storage

    def add_studio(self) -> tuple[StudioLibrary, DeliveryAcknowledger]:
        """The studio's real receiver app at studio.test:8720, listed as online."""
        library = StudioLibrary(self.tmp_path / "studio-box")
        acknowledger = DeliveryAcknowledger(
            make_api_client(self.network), library, Event(), retry_delay_seconds=0
        )
        self.network.add_app(STUDIO_URL, build_receiver_app(library, acknowledger, "studio-1"))
        self.orchestrator.peers.append(
            Peer(
                worker_id="studio-1",
                name="studio",
                role=WorkerRole.STUDIO,
                tailnet_address="studio.test",
                file_server_port=None,
                receiver_port=8720,
                online=True,
                last_heartbeat=None,
            )
        )
        return library, acknowledger


def build_harness(
    tmp_path: Path,
    role: WorkerRole = WorkerRole.WORKER,
    cap_bytes: int = 10 * GB,
    disk_free_bytes: int = 500 * GB,
) -> WorkerHarness:
    orchestrator = FakeOrchestrator(tmp_path / "vps", role=role)
    network = RoutingTransport()
    network.add_app(BASE_URL, orchestrator.app)
    media = FakeMediaTools()
    storage = Storage(
        tmp_path / "worker",
        cap_bytes,
        ignores_cap_for_raws=role == WorkerRole.MASTER,
        disk_free=lambda _: disk_free_bytes,
    )
    api = make_api_client(network)
    leases = LeaseTracker()
    measurements = MeasurementLog()
    shutdown = Event()
    status = StatusBoard()
    library = StudioLibrary(tmp_path / "box") if role == WorkerRole.STUDIO else None
    env = TaskEnvironment(
        api=api,
        storage=storage,
        media=media,
        measurements=measurements,
        transfer_transport=network,
        settings=FAST_SETTINGS,
        studio_library=library,
    )
    heartbeater = Heartbeater(
        api=api,
        role=role,
        storage=storage,
        leases=leases,
        measurements=measurements,
        status=status,
        tailnet_address="worker.test",
        file_server_port=8710,
        receiver_port=8720,
    )
    stage_freed = Event()
    stages = {
        kind: StageRunner(kind, TASK_HANDLERS[kind], env, leases, shutdown, stage_freed, status)
        for kind in STAGE_KINDS_BY_ROLE[role]
    }
    dispatcher = Dispatcher(
        api=api,
        role=role,
        stages=stages,
        leases=leases,
        heartbeater=heartbeater,
        storage=storage,
        shutdown=shutdown,
        stage_freed=stage_freed,
    )
    return WorkerHarness(
        orchestrator, network, media, storage, env, leases, shutdown, status,
        heartbeater, stages, dispatcher, tmp_path,
    )  # fmt: skip


@pytest.fixture
def harness(tmp_path) -> WorkerHarness:
    """A volunteer worker (role "worker") with a 10 GB cap and plenty of disk."""
    return build_harness(tmp_path)


@pytest.fixture
def raw_bytes() -> bytes:
    """About 200 KB of non-repeating bytes standing in for a raw video."""
    return bytes((index * 31 + index // 97) % 256 for index in range(200_000))
