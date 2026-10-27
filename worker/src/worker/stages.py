"""One background thread per task kind ("stage"), fed by the dispatcher.

Design §5: a worker holds at most one job per stage. Each StageRunner owns
a one-slot queue; the dispatcher only claims a task of a kind whose stage
is idle and hands it over with submit(). So downloading job A, rendering
the proxy of job B and processing job C can all run at the same time.

The runner turns the handler's outcome into the right report:
  success          nothing more (the handler's last call ended the lease)
  TaskFailedError       POST /jobs/{id}/failed with the message and exit code
  TaskAbandonedError    stop renewing the lease; it expires and is re-offered
  TaskCancelledError    lease lost or shutdown: just stop
  ApiError         an unexpected refusal: treated like TaskAbandonedError
  anything else    a bug in the worker: reported as failed, so a human
                   sees it instead of the job bouncing between workers
"""

import logging
import queue
import threading
from collections.abc import Callable
from dataclasses import dataclass

from shared.models import FailedRequest, TaskKind
from worker.api_client import ApiError, OrchestratorUnreachableError
from worker.leases import ActiveLease, CancelToken, LeaseTracker, TaskCancelledError
from worker.logging_setup import job_context
from worker.status import StatusBoard
from worker.tasks.environment import TaskAbandonedError, TaskEnvironment, TaskFailedError

logger = logging.getLogger(__name__)

MAX_FAILURE_MESSAGE_CHARS = 4000  # FailedRequest.message limit
# Reasons a failure report may not get through; the lease then simply expires.
_REPORT_PROBLEMS = (TaskCancelledError, TaskAbandonedError, ApiError, OrchestratorUnreachableError)

TaskHandler = Callable[[TaskEnvironment, ActiveLease, object, CancelToken], None]


@dataclass(frozen=True)
class ClaimedTask:
    lease: ActiveLease
    task: object  # one of the shared task models; its `kind` matches the stage


class StageRunner:
    def __init__(
        self,
        kind: TaskKind,
        handler: TaskHandler,
        env: TaskEnvironment,
        leases: LeaseTracker,
        shutdown: threading.Event,
        stage_freed: threading.Event,
        status: StatusBoard,
    ) -> None:
        self.kind = kind
        self._handler = handler
        self._env = env
        self._leases = leases
        self._shutdown = shutdown
        # Set whenever this stage becomes idle, so the dispatcher asks
        # /next again at once instead of waiting out its poll delay.
        self._stage_freed = stage_freed
        self._status = status
        self._inbox: queue.Queue[ClaimedTask] = queue.Queue(maxsize=1)
        self._busy = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def is_idle(self) -> bool:
        return not self._busy.is_set()

    def submit(self, claimed: ClaimedTask) -> None:
        self._busy.set()
        self._inbox.put_nowait(claimed)

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name=f"stage-{self.kind}", daemon=True)
        self._thread.start()

    def join(self, timeout: float) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

    def run_submitted_now(self) -> None:
        """Run the submitted task on the calling thread (tests use this)."""
        self.execute(self._inbox.get_nowait())

    def _run(self) -> None:
        while not self._shutdown.is_set():
            try:
                claimed = self._inbox.get(timeout=0.5)
            except queue.Empty:
                continue
            self.execute(claimed)

    def execute(self, claimed: ClaimedTask) -> None:
        """Run one claimed task to its end (also called directly by tests)."""
        job_id = claimed.task.job_id
        cancel = CancelToken(claimed.lease, self._shutdown)
        self._status.task_started(self.kind, f"job {job_id}")
        with job_context(job_id):
            logger.info("starting %s (lease %s)", self.kind, claimed.lease.lease_id)
            try:
                self._handler(self._env, claimed.lease, claimed.task, cancel)
                logger.info("%s finished", self.kind)
            except TaskCancelledError as cancelled:
                logger.warning("%s stopped: %s", self.kind, cancelled.reason)
            except TaskAbandonedError as abandoned:
                logger.warning("%s abandoned, lease left to expire: %s", self.kind, abandoned)
            except TaskFailedError as failed:
                logger.error("%s failed: %s", self.kind, failed.message)
                self._report_failure(claimed, cancel, failed.message, failed.exit_code)
            except ApiError as error:
                logger.warning("%s abandoned after the orchestrator refused: %s", self.kind, error)
            except Exception as error:
                logger.exception("%s crashed with an unexpected error", self.kind)
                message = f"internal worker error during {self.kind}: {error!r}"
                self._report_failure(claimed, cancel, message, None)
            finally:
                self._leases.release(claimed.lease.lease_id)
                self._status.task_finished(self.kind)
                self._busy.clear()
                self._stage_freed.set()

    def _report_failure(
        self, claimed: ClaimedTask, cancel: CancelToken, message: str, exit_code: int | None
    ) -> None:
        request = FailedRequest(
            lease_id=claimed.lease.lease_id,
            message=message[-MAX_FAILURE_MESSAGE_CHARS:],
            exit_code=exit_code,
        )
        job_id = claimed.task.job_id
        try:
            answer = self._env.report(
                lambda: self._env.api.report_failed(job_id, request), cancel, "failure report"
            )
            logger.info("failure reported; job is now %s", answer.state)
        except _REPORT_PROBLEMS as error:
            logger.warning("could not report the failure (%s); the lease will expire", error)
