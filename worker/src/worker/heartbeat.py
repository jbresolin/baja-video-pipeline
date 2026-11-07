"""Heartbeat every 15 s (docs/api.md §5).

Each beat tells the orchestrator: free space under the cap, where to reach
us (tailnet address, ports), which leases we still work under (listing a
lease renews it), which raws we hold, and what we measured since the last
beat. The answer tells us:
  * lease_status: `lost` → the task under that lease must stop;
  * accepting_new_raws: false → do not ask for download_raw (near the cap);
  * deletable_job_ids: raws of discarded jobs we may delete;
  * archived_clip_sha256s: kept clips the master has archived; near the
    cap we evict those (oldest first), never any other (worker.clip_store);
  * role: the role the orchestrator has for us (an admin may switch master
    and worker); handed to `on_role_answer` (worker.role_change).

Each beat also declares our render features (worker.render_features), so
the orchestrator offers us only renders we can make. An orchestrator older
than contract 2.6.0 refuses that unknown field with 400; the beat is then
sent again without it, and so is every beat for the next 5 minutes (after
which we try again, in case the VPS was updated). Such an orchestrator
never asks for a feature anyway.
"""

import logging
import threading
import time
from collections.abc import Callable

from shared.models import HeartbeatRequest, HeartbeatResponse, LeaseHeartbeatStatus, WorkerRole
from shared.protocol import HEARTBEAT_INTERVAL_SECONDS
from worker.api_client import ApiError, OrchestratorClient, OrchestratorUnreachableError
from worker.config import CONNECTION_STEP_PENDING_MESSAGE
from worker.leases import LeaseTracker
from worker.pause import PauseSwitch
from worker.render_features import DECLARED_RENDER_FEATURES
from worker.status import StatusBoard
from worker.storage import Storage
from worker.tasks.environment import MeasurementLog

logger = logging.getLogger(__name__)

# What the orchestrator answers to a bearer token it does not accept.
UNAUTHENTICATED_STATUS = 401
# After an orchestrator refused `render_features`, beats leave it out for
# this long before trying again (as dispatcher.py does for task kinds).
RETRY_RENDER_FEATURES_AFTER_SECONDS = 300.0


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
        connection_step_pending: bool = False,
        pause: PauseSwitch | None = None,
        on_role_answer: Callable[[WorkerRole], None] | None = None,
        shared_tailnet: bool = False,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        """`fixed_interval_seconds` overrides the orchestrator's
        heartbeat_interval_s (in-process test runs beat faster).
        `connection_step_pending` (WorkerConfig.connection_step_pending):
        the tailnet join is not done, so a failed beat is explained with
        CONNECTION_STEP_PENDING_MESSAGE rather than the network error.
        `pause`: the volunteer's switch; each beat reports `paused`.
        `on_role_answer`: called with the answer's `role` after every
        successful beat (worker.role_change decides what to do with it).
        `shared_tailnet`: WorkerConfig.shared_tailnet, reported in every beat.
        `monotonic`: the steady clock (tests move it by hand)."""
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
        self._connection_step_pending = connection_step_pending
        self._pause = pause
        self._on_role_answer = on_role_answer
        self._shared_tailnet = shared_tailnet
        self._monotonic = monotonic
        # When an orchestrator last refused our render_features (steady clock).
        self._render_features_refused_at: float | None = None
        self.interval_seconds: float = fixed_interval_seconds or HEARTBEAT_INTERVAL_SECONDS
        # How many of our kept clips the master has archived (status page).
        self.clips_archived_by_master = 0
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
            held_clips=[] if is_studio else self._storage.clips.held_clips(),
            paused=self._pause is not None and self._pause.is_paused,
            # Also asks the orchestrator to answer with our role (role_change).
            role=self._role,
            shared_tailnet=self._shared_tailnet,
            render_features=None
            if self._render_features_refused_recently()
            else (DECLARED_RENDER_FEATURES),
        )

    def _render_features_refused_recently(self) -> bool:
        refused_at = self._render_features_refused_at
        if refused_at is None:
            return False
        return self._monotonic() - refused_at < RETRY_RENDER_FEATURES_AFTER_SECONDS

    def _send(self, request: HeartbeatRequest) -> HeartbeatResponse:
        """POST the beat; resend it without render_features if the orchestrator refuses them."""
        try:
            return self._api.heartbeat(request)
        except ApiError as error:
            if request.render_features is None or not refuses_render_features(error):
                raise
        logger.info(
            "the orchestrator does not know render_features yet (older than contract 2.6.0);"
            " leaving them out of heartbeats for %d s",
            RETRY_RENDER_FEATURES_AFTER_SECONDS,
        )
        self._render_features_refused_at = self._monotonic()
        return self._api.heartbeat(request.model_copy(update={"render_features": None}))

    def beat_once(self) -> HeartbeatResponse | None:
        """Send one heartbeat and act on the answer; None if it failed."""
        request = self.build_request()
        self._publish_storage(request)
        try:
            answer = self._send(request)
        except (OrchestratorUnreachableError, ApiError) as error:
            # Leases keep running on the orchestrator's side for 45 s, so a
            # single missed beat is harmless; just keep the measurements.
            self._measurements.put_back(request.measurements)
            if self._connection_step_pending and isinstance(error, OrchestratorUnreachableError):
                self._status.set("orchestrator", CONNECTION_STEP_PENDING_MESSAGE)
            else:
                self._status.set("orchestrator", f"unreachable: {error}")
            self._status.record_unreachable(str(error))
            if isinstance(error, ApiError) and error.status_code == UNAUTHENTICATED_STATUS:
                # Our bearer token is refused: an admin revoked this worker
                # (design §21). Only "Registrar de novo" gets it working again,
                # so the status page and the tray offer exactly that.
                self._status.set_identity_rejected(True)
            logger.warning("heartbeat failed: %s", error)
            return None

        self._status.record_contact()
        self._status.set_identity_rejected(False)
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
        # The studio never takes raws, so "no" is normal there, not "disk full".
        is_studio = self._role == WorkerRole.STUDIO
        self._status.set_near_cap(not answer.accepting_new_raws and not is_studio)
        for job_id in answer.deletable_job_ids:
            self._storage.delete_raw(job_id)
        archived = set(answer.archived_clip_sha256s)
        self._storage.clips.evict_archived(archived, self._storage.free_bytes_under_cap)
        self.clips_archived_by_master = len(archived)
        self._publish_clips()
        if answer.role is not None and self._on_role_answer is not None:
            self._on_role_answer(answer.role)
        return answer

    def _publish_clips(self) -> None:
        """Clips kept / archived, for the status page (design §11 as amended)."""
        if self._role == WorkerRole.STUDIO:
            return
        kept = len(self._storage.clips.held_clips())
        if self._role == WorkerRole.MASTER:
            # The master's archive is its own: everything in it is archived.
            in_archive = self._storage.clips.archived_clip_count()
            self._status.set_clips(held=kept + in_archive, archived=in_archive)
        else:
            self._status.set_clips(held=kept, archived=self.clips_archived_by_master)

    def _publish_storage(self, request: HeartbeatRequest) -> None:
        """Space used vs. cap and the held-raw count, for the status page."""
        self._status.set_storage(
            used_bytes=self._storage.used_bytes(),
            cap_bytes=self._storage.cap_bytes,
            held_raws=len(request.held_job_ids),
        )

    def run_forever(self, shutdown: threading.Event) -> None:
        """Thread body: beat now, then every interval until shutdown."""
        while not shutdown.is_set():
            try:
                self.beat_once()
            except Exception:
                logger.exception("unexpected error in the heartbeat loop")
            shutdown.wait(self.interval_seconds)


def refuses_render_features(error: ApiError) -> bool:
    """True for an older orchestrator's 400 naming the field it does not know.

    Its answer looks like {"code": "validation_error", "details": {"errors":
    [{"loc": ["body", "render_features"], "msg": "Extra inputs are not permitted"}]}}.
    """
    if error.status_code != 400 or error.code != "validation_error":
        return False
    problems = error.details.get("errors") or []
    return any("render_features" in (problem.get("loc") or []) for problem in problems)
