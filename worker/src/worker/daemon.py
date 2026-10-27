"""Wires every piece together once the worker is enrolled, and stops them cleanly.

Threads started (all daemon threads; stop() ends them):
    heartbeat             every 15 s
    dispatcher            /next + /claim, hands tasks to the stages
    stage-<kind>          one per task kind of the role (design §5)
    peer-file-server      :8710 (master, worker)
    studio-receiver       :8720 and the ACK sender (studio)
    backfill              master only

Clean shutdown (SIGTERM / CTRL-C): the shared `shutdown` event makes the
dispatcher stop claiming and every running task stop at its next check:
downloads keep their ".part" file (resumed next time), ffmpeg is
terminated and its tmp output deleted, uploads stop between chunks (the
receiver keeps what it has), finished clips stay in clips/ for reuse.
Every file is written as a hidden ".part" and renamed when complete, so
stopping at any moment never leaves a corrupt visible file. Leases are
simply left to expire.
"""

import logging
import threading
from pathlib import Path

import httpx

from shared.models import TaskKind, WorkerRole
from worker.api_client import OrchestratorClient
from worker.backfill import MasterBackfill
from worker.cancellable_transport import transfer_client_for
from worker.config import WorkerConfig
from worker.dispatcher import STAGE_KINDS_BY_ROLE, Dispatcher
from worker.enrollment import detect_tailnet_address
from worker.heartbeat import Heartbeater
from worker.leases import CancelToken, LeaseTracker
from worker.media_tools import MediaTools
from worker.servers import BackgroundServer, build_peer_file_server_app
from worker.stages import StageRunner
from worker.status import StatusBoard
from worker.storage import Storage
from worker.studio.acknowledger import DeliveryAcknowledger
from worker.studio.library import StudioLibrary
from worker.studio.receiver_app import build_receiver_app
from worker.tasks import TASK_HANDLERS
from worker.tasks.environment import MeasurementLog, TaskEnvironment

logger = logging.getLogger(__name__)

API_TIMEOUT = httpx.Timeout(30.0, connect=10.0)
STOP_TIMEOUT_SECONDS = 20.0


def build_api_client(config: WorkerConfig, transport: httpx.BaseTransport) -> OrchestratorClient:
    return OrchestratorClient(
        httpx.Client(transport=transport, timeout=API_TIMEOUT),
        config.orchestrator_url,
        worker_id=config.worker_id,
        bearer_token=config.bearer_token,
    )


class Daemon:
    def __init__(
        self,
        config: WorkerConfig,
        config_path: Path,
        status: StatusBoard,
        shutdown: threading.Event,
        *,
        media: MediaTools,
        transport: httpx.BaseTransport | None = None,
        start_servers: bool = True,
    ) -> None:
        """`transport` and `start_servers=False` exist for tests."""
        if not config.is_enrolled or config.role is None:
            raise ValueError("the daemon needs an enrolled config")
        self.config = config
        self.role: WorkerRole = config.role
        self.status = status
        self.shutdown = shutdown
        self._start_servers = start_servers
        self._threads: list[threading.Thread] = []
        self._servers: list[BackgroundServer] = []
        transport = transport or httpx.HTTPTransport()

        working_folder = config.resolved_working_folder(config_path)
        self.storage = Storage(
            working_folder,
            config.storage_cap_bytes,
            ignores_cap_for_raws=self.role == WorkerRole.MASTER,
        )
        self.api = build_api_client(config, transport)
        self.leases = LeaseTracker()
        self.measurements = MeasurementLog()
        self.library = None
        if self.role == WorkerRole.STUDIO:
            self.library = StudioLibrary(config.resolved_studio_clip_root(config_path))
        self.env = TaskEnvironment(
            api=self.api,
            storage=self.storage,
            media=media,
            measurements=self.measurements,
            transfer_transport=transport,
            studio_library=self.library,
        )
        self.tailnet_address = config.tailnet_address or detect_tailnet_address()
        self.heartbeater = Heartbeater(
            api=self.api,
            role=self.role,
            storage=self.storage,
            leases=self.leases,
            measurements=self.measurements,
            status=status,
            tailnet_address=self.tailnet_address,
            file_server_port=config.file_server_port,
            receiver_port=config.receiver_port,
        )
        self.stage_freed = threading.Event()
        self.stages: dict[TaskKind, StageRunner] = {
            kind: StageRunner(
                kind, TASK_HANDLERS[kind], self.env, self.leases, shutdown, self.stage_freed, status
            )
            for kind in STAGE_KINDS_BY_ROLE[self.role]
        }
        self.dispatcher = Dispatcher(
            api=self.api,
            role=self.role,
            stages=self.stages,
            leases=self.leases,
            heartbeater=self.heartbeater,
            storage=self.storage,
            shutdown=shutdown,
            stage_freed=self.stage_freed,
        )
        self.acknowledger = None
        if self.library is not None:
            self.acknowledger = DeliveryAcknowledger(self.api, self.library, shutdown)
        self.backfill = None
        if self.role == WorkerRole.MASTER:
            self.backfill = MasterBackfill(
                api=self.api,
                storage=self.storage,
                measurements=self.measurements,
                transfer_client=transfer_client_for(transport, CancelToken(None, shutdown)),
                own_worker_id=config.worker_id or "",
            )

    def start(self) -> None:
        logger.info("starting as %s (%s)", self.role, self.config.worker_id)
        self.storage.clean_up_at_start()
        self.status.set("working folder", str(self.storage.root))
        self._thread("heartbeat", self.heartbeater.run_forever, self.shutdown)
        for stage in self.stages.values():
            stage.start()
        self._thread("dispatcher", self.dispatcher.run_forever)
        if self.acknowledger is not None:
            self._thread("studio-acks", self.acknowledger.run_forever)
        if self.backfill is not None:
            self._thread("backfill", self.backfill.run_forever, self.shutdown)
        if self._start_servers:
            self._start_http_servers()

    def _start_http_servers(self) -> None:
        host = self.config.listen_address or self.tailnet_address or "0.0.0.0"
        if host == "0.0.0.0":
            logger.warning("no tailnet address known; file servers listen on all interfaces")
        worker_id = self.config.worker_id or ""
        if self.role == WorkerRole.STUDIO:
            app = build_receiver_app(self.library, self.acknowledger, worker_id)
            server = BackgroundServer(app, host, self.config.receiver_port, "studio-receiver")
        else:
            app = build_peer_file_server_app(self.storage, worker_id, self.role.value)
            server = BackgroundServer(app, host, self.config.file_server_port, "peer-file-server")
        server.start()
        self._servers.append(server)

    def _thread(self, name: str, target, *args) -> None:
        thread = threading.Thread(target=target, args=args, name=name, daemon=True)
        thread.start()
        self._threads.append(thread)

    def stop(self) -> None:
        """Ask everything to stop and wait (bounded) for running tasks to wind down."""
        logger.info("shutting down")
        self.shutdown.set()
        self.stage_freed.set()  # wake the dispatcher
        for stage in self.stages.values():
            stage.join(STOP_TIMEOUT_SECONDS)
        for thread in self._threads:
            thread.join(STOP_TIMEOUT_SECONDS)
        for server in self._servers:
            server.stop()
        self.api.http.close()
        logger.info("stopped")
