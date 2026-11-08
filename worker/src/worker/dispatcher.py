"""Pull work: ask /next for the kinds whose stage is idle, claim, hand over.

docs/api.md §5:
  * /next returns an *offer*; only /claim takes a lease. A 409
    already_claimed or not_claimable just means "ask again".
  * Leave a kind out of accept_kinds while its stage is busy.
  * `task: null` → wait retry_after_s (or until a stage frees up).

Which kinds a role asks for:
  master  download_raw, make_proxy, process, archive_clip. download_raw is
          asked for even when the heartbeat said accepting_new_raws=false:
          the master holds every raw (design §4); only real disk space
          stops it. archive_clip: it keeps every finished clip (§11).
  worker  the same three; download_raw only while accepting_new_raws.
  studio  deliver (it never holds raws, design §13), and upload_clip
          ("Baixar clipe", LIVESTACK-52) only while it is not delivering:
          the orchestrator offers it the upload only as a last resort.

While the volunteer has paused the worker (worker.pause), nothing is asked
for at all: the running tasks finish, no new one starts.
"""

import logging
import threading
import time
from collections.abc import Callable

from shared.models import DownloadRawTask, TaskKind, WorkerRole
from shared.protocol import EMPTY_QUEUE_RETRY_SECONDS
from worker.api_client import ApiError, OrchestratorClient, OrchestratorUnreachableError
from worker.heartbeat import Heartbeater
from worker.leases import LeaseTracker
from worker.pause import PauseSwitch
from worker.stages import ClaimedTask, StageRunner
from worker.storage import Storage

logger = logging.getLogger(__name__)

STAGE_KINDS_BY_ROLE = {
    WorkerRole.MASTER: [
        TaskKind.DOWNLOAD_RAW,
        TaskKind.MAKE_PROXY,
        TaskKind.PROCESS,
        TaskKind.ARCHIVE_CLIP,
        TaskKind.UPLOAD_ORIGINAL,
        TaskKind.UPLOAD_CLIP,
    ],
    WorkerRole.WORKER: [
        TaskKind.DOWNLOAD_RAW,
        TaskKind.MAKE_PROXY,
        TaskKind.PROCESS,
        TaskKind.UPLOAD_ORIGINAL,
        TaskKind.UPLOAD_CLIP,
    ],
    WorkerRole.STUDIO: [TaskKind.DELIVER, TaskKind.UPLOAD_CLIP],
}
UNREACHABLE_RETRY_SECONDS = 10.0
# Kinds newer than some orchestrators: one that does not know a kind answers
# 400 validation_error to the whole /next request, so such a kind is left
# out from then on (until the worker restarts) instead of blocking all work.
# upload_original: LIVESTACK-41 item 15 (installer 0.3.0); upload_clip:
# LIVESTACK-52 (installer 0.7.0).
NEWER_TASK_KINDS = (TaskKind.UPLOAD_ORIGINAL, TaskKind.UPLOAD_CLIP)
# ...but only for a while: the VPS is often updated after the workers, and a
# worker that stopped asking for good would never pick the kind up again
# until someone restarted it ("Baixar original" stuck on "Preparando").
ASK_AGAIN_FOR_REFUSED_KIND_SECONDS = 300.0


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
        pause: PauseSwitch | None = None,
        seconds_since_start: Callable[[], float] = time.monotonic,
    ) -> None:
        """`max_wait_seconds` caps every pause between rounds (the
        orchestrator's retry_after_s, the 10 s after a network error); in-
        process test runs use it. None = wait as long as asked.
        `seconds_since_start` is a steady clock; tests pass a fake one."""
        self._api = api
        self._role = role
        self._stages = stages
        self._leases = leases
        self._heartbeater = heartbeater
        self._storage = storage
        self._shutdown = shutdown
        self._stage_freed = stage_freed
        self._max_wait_seconds = max_wait_seconds
        self._pause = pause
        self._seconds_since_start = seconds_since_start
        # Kind → when the orchestrator last refused it (steady-clock seconds).
        self._kinds_the_orchestrator_refused: dict[TaskKind, float] = {}

    def kinds_to_ask_for(self) -> list[TaskKind]:
        if self._pause is not None and self._pause.is_paused:
            return []
        kinds = []
        for kind in STAGE_KINDS_BY_ROLE[self._role]:
            if not self._stages[kind].is_idle or self._refused_recently(kind):
                continue
            if kind == TaskKind.DOWNLOAD_RAW and not self._may_take_new_raws():
                continue
            if kind == TaskKind.UPLOAD_CLIP and self._studio_is_delivering():
                continue
            kinds.append(kind)
        return kinds

    def _refused_recently(self, kind: TaskKind) -> bool:
        """Did the orchestrator refuse this kind less than five minutes ago?"""
        refused_at = self._kinds_the_orchestrator_refused.get(kind)
        if refused_at is None:
            return False
        seconds_since_refusal = self._seconds_since_start() - refused_at
        return seconds_since_refusal < ASK_AGAIN_FOR_REFUSED_KIND_SECONDS

    def _studio_is_delivering(self) -> bool:
        """The studio pulls clips first; a reviewer's download can wait."""
        if self._role != WorkerRole.STUDIO:
            return False
        return not self._stages[TaskKind.DELIVER].is_idle

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
        except ApiError as error:
            newer = [kind for kind in kinds if kind in NEWER_TASK_KINDS]
            if error.code == "validation_error" and newer:
                # An orchestrator from before these kinds: stop asking for them
                # for a while, then try again (it may have been updated).
                logger.warning(
                    "the orchestrator does not know %s; not asking for it for %d s",
                    newer,
                    ASK_AGAIN_FOR_REFUSED_KIND_SECONDS,
                )
                refused_at = self._seconds_since_start()
                for kind in newer:
                    self._kinds_the_orchestrator_refused[kind] = refused_at
                return 0.0
            logger.warning("asking for work failed: %s", error)
            return UNREACHABLE_RETRY_SECONDS
        except OrchestratorUnreachableError as error:
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
