"""Pull work: ask /next for the kinds whose stage is idle, claim, hand over.

docs/api.md §5:
  * /next returns an *offer*; only /claim takes a lease. A 409
    already_claimed or not_claimable just means "ask again".
  * Leave a kind out of accept_kinds while its stage is busy.
  * `task: null` → wait retry_after_s (or until a stage frees up).

Which kinds a role asks for:
  master  download_raw, make_proxy, process. download_raw is asked for
          even when the heartbeat said accepting_new_raws=false: the
          master holds every raw (design §4); only real disk space stops it.
  worker  the same three; download_raw only while accepting_new_raws.
  studio  deliver only (it never holds raws, design §13).
"""

import logging
import threading

from shared.models import DownloadRawTask, TaskKind, WorkerRole
from shared.protocol import EMPTY_QUEUE_RETRY_SECONDS
from worker.api_client import ApiError, OrchestratorClient, OrchestratorUnreachableError
from worker.heartbeat import Heartbeater
from worker.leases import LeaseTracker
from worker.stages import ClaimedTask, StageRunner
from worker.storage import Storage

logger = logging.getLogger(__name__)

STAGE_KINDS_BY_ROLE = {
    WorkerRole.MASTER: [TaskKind.DOWNLOAD_RAW, TaskKind.MAKE_PROXY, TaskKind.PROCESS],
    WorkerRole.WORKER: [TaskKind.DOWNLOAD_RAW, TaskKind.MAKE_PROXY, TaskKind.PROCESS],
    WorkerRole.STUDIO: [TaskKind.DELIVER],
}
UNREACHABLE_RETRY_SECONDS = 10.0


class Dispatcher:
    def __init__(
        self,
        *,
        api: OrchestratorClient,
        role: WorkerRole,
        stages: dict[TaskKind, StageRunner],
        leases: LeaseTracker,
        heartbeater: Heartbeater,
        storage: Storage,
        shutdown: threading.Event,
        stage_freed: threading.Event,
        max_wait_seconds: float | None = None,
    ) -> None:
        """`max_wait_seconds` caps every pause between rounds (the
        orchestrator's retry_after_s, the 10 s after a network error); in-
        process test runs use it. None = wait as long as asked."""
        self._api = api
        self._role = role
        self._stages = stages
        self._leases = leases
        self._heartbeater = heartbeater
        self._storage = storage
        self._shutdown = shutdown
        self._stage_freed = stage_freed
        self._max_wait_seconds = max_wait_seconds

    def kinds_to_ask_for(self) -> list[TaskKind]:
        kinds = []
        for kind in STAGE_KINDS_BY_ROLE[self._role]:
            if not self._stages[kind].is_idle:
                continue
            if kind == TaskKind.DOWNLOAD_RAW and not self._may_take_new_raws():
                continue
            kinds.append(kind)
        return kinds

    def _may_take_new_raws(self) -> bool:
        if self._role == WorkerRole.MASTER:
            return True
        return self._heartbeater.accepting_new_raws

    def run_once(self) -> float:
        """Ask for and claim at most one task. Returns how long to wait before
        the next round (0 = go again at once)."""
        kinds = self.kinds_to_ask_for()
        if not kinds:
            return EMPTY_QUEUE_RETRY_SECONDS  # all busy; a freed stage wakes us earlier
        try:
            offer = self._api.next_task(kinds)
        except (OrchestratorUnreachableError, ApiError) as error:
            logger.warning("asking for work failed: %s", error)
            return UNREACHABLE_RETRY_SECONDS
        task = offer.task
        if task is None:
            return float(offer.retry_after_s)

        if isinstance(task, DownloadRawTask) and not self._storage.can_hold_new_raw(
            task.size_bytes
        ):
            logger.warning(
                "offered raw of %d bytes does not fit; not taking new raws for now",
                task.size_bytes,
                extra={"job_id": task.job_id},
            )
            self._heartbeater.stop_accepting_new_raws()
            return 0.0

        try:
            claim = self._api.claim(task.job_id, task.kind)
        except ApiError as error:
            # Someone else won, or the job moved on: simply ask again.
            logger.info("claim refused (%s)", error.code, extra={"job_id": task.job_id})
            return 0.0
        except OrchestratorUnreachableError as error:
            logger.warning("claim failed: %s", error, extra={"job_id": task.job_id})
            return UNREACHABLE_RETRY_SECONDS

        stage = self._stages.get(claim.task.kind)
        if stage is None or not stage.is_idle:
            # Never expected (we only ask for idle kinds); let the lease expire.
            logger.error("got a %s task we cannot run now", claim.task.kind)
            return 0.0
        active_lease = self._leases.add(claim.lease)
        logger.info(
            "claimed %s (lease %s)", claim.task.kind, claim.lease.lease_id,
            extra={"job_id": task.job_id},
        )  # fmt: skip
        stage.submit(ClaimedTask(active_lease, claim.task))
        return 0.0

    def run_forever(self) -> None:
        """Thread body."""
        while not self._shutdown.is_set():
            try:
                wait_seconds = self.run_once()
            except Exception:
                logger.exception("unexpected error in the dispatcher")
                wait_seconds = UNREACHABLE_RETRY_SECONDS
            if self._max_wait_seconds is not None:
                wait_seconds = min(wait_seconds, self._max_wait_seconds)
            if wait_seconds > 0:
                # Wake early when a stage finishes, or at shutdown.
                self._stage_freed.wait(wait_seconds)
            self._stage_freed.clear()
