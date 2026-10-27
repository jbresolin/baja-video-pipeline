"""Heartbeat every 15 s (docs/api.md §5).

Each beat tells the orchestrator: free space under the cap, where to reach
us (tailnet address, ports), which leases we still work under (listing a
lease renews it), which raws we hold, and what we measured since the last
beat. The answer tells us:
  * lease_status: `lost` → the task under that lease must stop;
  * accepting_new_raws: false → do not ask for download_raw (near the cap);
  * deletable_job_ids: raws of discarded jobs we may delete.
"""

import logging
import threading

from shared.models import HeartbeatRequest, HeartbeatResponse, LeaseHeartbeatStatus, WorkerRole
from shared.protocol import HEARTBEAT_INTERVAL_SECONDS
from worker.api_client import ApiError, OrchestratorClient, OrchestratorUnreachableError
from worker.leases import LeaseTracker
from worker.status import StatusBoard
from worker.storage import Storage
from worker.tasks.environment import MeasurementLog

logger = logging.getLogger(__name__)


class Heartbeater:
    def __init__(
        self,
        *,
        api: OrchestratorClient,
        role: WorkerRole,
        storage: Storage,
        leases: LeaseTracker,
        measurements: MeasurementLog,
        status: StatusBoard,
        tailnet_address: str | None,
        file_server_port: int,
        receiver_port: int,
        fixed_interval_seconds: float | None = None,
    ) -> None:
        """`fixed_interval_seconds` overrides the orchestrator's
        heartbeat_interval_s (in-process test runs beat faster)."""
        self._api = api
        self._role = role
        self._storage = storage
        self._leases = leases
        self._measurements = measurements
        self._status = status
        self._tailnet_address = tailnet_address
        self._file_server_port = file_server_port
        self._receiver_port = receiver_port
        self._fixed_interval_seconds = fixed_interval_seconds
        self.interval_seconds: float = fixed_interval_seconds or HEARTBEAT_INTERVAL_SECONDS
        # Until the first answer arrives, assume we may take new raws.
        self._accepting_new_raws = threading.Event()
        self._accepting_new_raws.set()

    @property
    def accepting_new_raws(self) -> bool:
        return self._accepting_new_raws.is_set()

    def stop_accepting_new_raws(self) -> None:
        """Local override until the next answer (an offered raw did not fit)."""
        self._accepting_new_raws.clear()

    def use_listening_ports(self, file_server_port: int, receiver_port: int) -> None:
        """The ports actually bound (they differ from the config when it says 0)."""
        self._file_server_port = file_server_port
        self._receiver_port = receiver_port

    def build_request(self) -> HeartbeatRequest:
        is_studio = self._role == WorkerRole.STUDIO
        return HeartbeatRequest(
            free_bytes=self._storage.free_bytes_under_cap(),
            storage_cap_bytes=self._storage.cap_bytes,
            tailnet_address=self._tailnet_address,
            file_server_port=None if is_studio else self._file_server_port,
            receiver_port=self._receiver_port if is_studio else None,
            current_lease_ids=self._leases.ids(),
            held_job_ids=[] if is_studio else self._storage.held_job_ids(),
            measurements=self._measurements.drain(),
        )

    def beat_once(self) -> HeartbeatResponse | None:
        """Send one heartbeat and act on the answer; None if it failed."""
        request = self.build_request()
        try:
            answer = self._api.heartbeat(request)
        except (OrchestratorUnreachableError, ApiError) as error:
            # Leases keep running on the orchestrator's side for 45 s, so a
            # single missed beat is harmless; just keep the measurements.
            self._measurements.put_back(request.measurements)
            self._status.set("orchestrator", f"unreachable: {error}")
            logger.warning("heartbeat failed: %s", error)
            return None

        self._status.set_now("last heartbeat")
        self._status.set("orchestrator", "connected")
        self.interval_seconds = (
            self._fixed_interval_seconds
            or answer.heartbeat_interval_s
            or HEARTBEAT_INTERVAL_SECONDS
        )
        for lease_id, lease_status in answer.lease_status.items():
            if lease_status == LeaseHeartbeatStatus.LOST:
                logger.info("lease %s reported lost", lease_id)
                self._leases.mark_lost(lease_id)
        if answer.accepting_new_raws:
            self._accepting_new_raws.set()
        else:
            self._accepting_new_raws.clear()
        self._status.set("accepting new raws", "yes" if answer.accepting_new_raws else "no")
        for job_id in answer.deletable_job_ids:
            self._storage.delete_raw(job_id)
        return answer

    def run_forever(self, shutdown: threading.Event) -> None:
        """Thread body: beat now, then every interval until shutdown."""
        while not shutdown.is_set():
            try:
                self.beat_once()
            except Exception:
                logger.exception("unexpected error in the heartbeat loop")
            shutdown.wait(self.interval_seconds)
