"""What every task handler receives (TaskEnvironment) and shared helpers.

A handler is `handler(env, lease, task, cancel)`. It returns normally on
success, or raises; worker.failures explains how each exception ends the
task (failure report with scope "job"/"worker", lease left to expire, or
cancelled) and holds the one table that decides the scope.
"""

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TypeVar

import httpx

from shared.models import Measurement, MeasurementKind, WorkerRole
from shared.protocol import DEFAULT_UPLOAD_CHUNK_BYTES
from worker.api_client import ApiError, OrchestratorClient, OrchestratorUnreachableError
from worker.cancellable_transport import transfer_client_for
from worker.failures import TaskAbandonedError
from worker.leases import REASON_LEASE_LOST, CancelToken, TaskCancelledError
from worker.media_tools import MediaTools
from worker.storage import Storage

logger = logging.getLogger(__name__)

T = TypeVar("T")

# How often a report (holder-confirm, ACK, failed) is retried while the
# orchestrator is unreachable, and how long to wait between tries.
REPORT_ATTEMPTS = 8
REPORT_RETRY_DELAY_SECONDS = 5.0
# Design §5 / docs/api.md §5: after a direct push, wait this long for the
# studio's ACK to end our lease before uploading to the outbox instead.
PUSH_ACK_WAIT_SECONDS = 60.0
TRANSFER_RETRY_DELAY_SECONDS = 1.0


class MeasurementLog:
    """Timed transfers and renders, collected until the next heartbeat."""

    def __init__(self) -> None:
        self._pending: list[Measurement] = []
        self._lock = threading.Lock()

    def record(self, kind: MeasurementKind, byte_count: int, started_at: float) -> None:
        """Record `byte_count` bytes handled since `started_at` (time.monotonic())."""
        seconds = time.monotonic() - started_at
        if byte_count <= 0 or seconds <= 0:
            return  # nothing meaningful to average
        with self._lock:
            self._pending.append(Measurement(kind=kind, byte_count=byte_count, seconds=seconds))

    def drain(self) -> list[Measurement]:
        with self._lock:
            taken, self._pending = self._pending, []
        return taken

    def put_back(self, measurements: list[Measurement]) -> None:
        """The heartbeat that carried them failed: send them next time."""
        with self._lock:
            self._pending = measurements + self._pending


@dataclass
class TaskSettings:
    push_ack_wait_seconds: float = PUSH_ACK_WAIT_SECONDS
    report_attempts: int = REPORT_ATTEMPTS
    report_retry_delay_seconds: float = REPORT_RETRY_DELAY_SECONDS
    upload_chunk_bytes: int = DEFAULT_UPLOAD_CHUNK_BYTES
    # Pause between retries inside one download/upload (network error, 5xx).
    transfer_retry_delay_seconds: float = TRANSFER_RETRY_DELAY_SECONDS


@dataclass
class TaskEnvironment:
    """Long-lived pieces the daemon builds once and hands to every task."""

    api: OrchestratorClient
    storage: Storage
    media: MediaTools
    measurements: MeasurementLog
    # Shared connection pool for downloads/uploads; each task wraps it.
    transfer_transport: httpx.BaseTransport
    settings: TaskSettings = field(default_factory=TaskSettings)
    # Studio only (worker.studio.library.StudioLibrary).
    studio_library: object | None = None
    # Our role; decides whether some failures are reported (worker.failures).
    role: WorkerRole | None = None

    def transfer_client(self, cancel: CancelToken) -> httpx.Client:
        return transfer_client_for(self.transfer_transport, cancel)

    def report(self, call: Callable[[], T], cancel: CancelToken, what: str) -> T:
        """Send a result report, retrying while the orchestrator is unreachable.

        409 lease_lost becomes TaskCancelledError (the result no longer counts);
        any other refusal is raised as ApiError for the handler to judge.
        """
        for attempt in range(1, self.settings.report_attempts + 1):
            try:
                return call()
            except OrchestratorUnreachableError as error:
                if attempt == self.settings.report_attempts:
                    # Reporting a failure would not get through either:
                    # leave the lease to expire.
                    raise TaskAbandonedError(f"could not send {what}: {error}") from error
                logger.warning("%s: orchestrator unreachable (%s); retrying", what, error)
                cancel.sleep(self.settings.report_retry_delay_seconds)
            except ApiError as error:
                if error.code == "lease_lost":
                    raise TaskCancelledError(REASON_LEASE_LOST) from error
                raise
        raise AssertionError("unreachable")
