"""Wires every piece together once the worker is enrolled, and stops them cleanly.

Threads started (all daemon threads; stop() ends them):
    heartbeat             every 15 s
    dispatcher            /next + /claim, hands tasks to the stages
    stage-<kind>          one per task kind of the role (design §5)
    peer-file-server      :8710 (master, worker)
    studio-receiver       :8720 and the ACK sender "studio-acks" (studio)

Clean shutdown (SIGTERM / CTRL-C): the shared `shutdown` event makes the
dispatcher stop claiming and every running task stop at its next check:
downloads keep their ".part" file (resumed next time), ffmpeg is
terminated and its tmp output deleted, uploads stop between chunks (the
receiver keeps what it has), finished clips stay in clips/ for reuse.
Every file is written as a hidden ".part" and renamed when complete, so
stopping at any moment never leaves a corrupt visible file. Leases are
simply left to expire.

The daemon installs no signal handlers (worker.__main__ does), so it also
runs inside a test process on any thread: build it, start(), stop().
"""

import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from shared.models import TaskKind, WorkerRole
from worker.api_client import OrchestratorClient
from worker.config import WorkerConfig
from worker.dispatcher import STAGE_KINDS_BY_ROLE, Dispatcher
from worker.enrollment import detect_tailnet_address
from worker.heartbeat import Heartbeater
from worker.leases import LeaseTracker
from worker.media_tools import MediaTools
from worker.servers import BackgroundServer, build_peer_file_server_app
from worker.stages import StageRunner
from worker.status import StatusBoard
from worker.storage import Storage
from worker.studio.acknowledger import RETRY_DELAY_SECONDS, DeliveryAcknowledger
from worker.studio.library import StudioLibrary
from worker.studio.pending_acks import PENDING_ACKS_FILE_NAME
from worker.studio.receiver_app import build_receiver_app
from worker.tasks import TASK_HANDLERS
from worker.tasks.environment import MeasurementLog, TaskEnvironment, TaskSettings

logger = logging.getLogger(__name__)

API_TIMEOUT = httpx.Timeout(30.0, connect=10.0)
STOP_TIMEOUT_SECONDS = 20.0


@dataclass
class DaemonTimings:
    """How long the daemon waits for things. The defaults are the real ones;
    tests and in-process end-to-end runs shorten them."""

    # Report retries, the 60 s wait for the studio's ACK after a push, chunk size.
    task_settings: TaskSettings = field(default_factory=TaskSettings)
    # None = what the orchestrator answers (15 s).
    heartbeat_interval_seconds: float | None = None
    # Studio: pause between rounds of unsent delivered ACKs.
    ack_retry_delay_seconds: float = RETRY_DELAY_SECONDS
    # Upper bound on the dispatcher's pause between /next rounds. None =
    # the orchestrator's retry_after_s (5 s) and 10 s after network errors.
    max_dispatch_wait_seconds: float | None = None


def build_api_client(config: WorkerConfig, transport: httpx.BaseTransport) -> OrchestratorClient:
    return OrchestratorClient(
        httpx.Client(transport=transport, timeout=API_TIMEOUT),
        config.orchestrator_url,
        worker_id=config.worker_id,
        bearer_token=config.bearer_token,
        tailnet_base_url=config.tailnet_base_url,
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
        timings: DaemonTimings | None = None,
    ) -> None:
        """`transport` (every HTTP request: API, downloads, uploads),
        `start_servers=False` and `timings` exist for tests."""
        if not config.is_enrolled or config.role is None:
            raise ValueError("the daemon needs an enrolled config")
        timings = timings or DaemonTimings()
        self.config = config
        self.role: WorkerRole = config.role
        self.status = status
        self.shutdown = shutdown
        self._start_servers = start_servers
        self._threads: list[threading.Thread] = []
        self._servers: list[BackgroundServer] = []
        # The port the 8710/8720 server really listens on, once started.
        self.listening_port: int | None = None
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
            settings=timings.task_settings,
            studio_library=self.library,
            role=self.role,
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
            fixed_interval_seconds=timings.heartbeat_interval_seconds,
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
            max_wait_seconds=timings.max_dispatch_wait_seconds,
        )
        self.acknowledger = None
        if self.library is not None:
            self.acknowledger = DeliveryAcknowledger(
                self.api,
                self.library,
                shutdown,
                retry_delay_seconds=timings.ack_retry_delay_seconds,
                pending_acks_path=working_folder / PENDING_ACKS_FILE_NAME,
            )

    def http_app(self):
        """The ASGI app this role serves on the tailnet: the studio receiver
        (:8720) or the peer file server (:8710). Tests may mount it on an
        in-memory transport instead of letting start() open a socket."""
        worker_id = self.config.worker_id or ""
        if self.role == WorkerRole.STUDIO:
            return build_receiver_app(self.library, self.acknowledger, worker_id)
        return build_peer_file_server_app(self.storage, worker_id, self.role.value)

    def start(self) -> None:
        logger.info("starting as %s (%s)", self.role, self.config.worker_id)
        self.storage.clean_up_at_start()
        self.status.set("working folder", str(self.storage.root))
        # Servers first, so the first heartbeat already carries the real port.
        if self._start_servers:
            self._start_http_server()
        self._thread("heartbeat", self.heartbeater.run_forever, self.shutdown)
        for stage in self.stages.values():
            stage.start()
        self._thread("dispatcher", self.dispatcher.run_forever)
        if self.acknowledger is not None:
            self._thread("studio-acks", self.acknowledger.run_forever)

    def _start_http_server(self) -> None:
        host = self.config.listen_address or self.tailnet_address or "0.0.0.0"
        if host == "0.0.0.0":
            logger.warning("no tailnet address known; file servers listen on all interfaces")
        if self.role == WorkerRole.STUDIO:
            port, name = self.config.receiver_port, "studio-receiver"
        else:
            port, name = self.config.file_server_port, "peer-file-server"
        server = BackgroundServer(self.http_app(), host, port, name)
        server.start()
        self._servers.append(server)
        try:
            self.listening_port = server.wait_until_listening()
        except RuntimeError:
            # Keep working without it: peers fall back to other sources and
            # pushes to the outbox. The log says why (port in use, …).
            logger.exception("%s could not start", name)
            return
        if self.role == WorkerRole.STUDIO:
            self.heartbeater.use_listening_ports(self.config.file_server_port, self.listening_port)
        else:
            self.heartbeater.use_listening_ports(self.listening_port, self.config.receiver_port)

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
