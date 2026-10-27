"""Leases we work under, and how a running task learns it must stop.

A task stops early for one of two reasons:
  * its lease was reported `lost` by a heartbeat (expired, or a reviewer
    changed the job): throw away partial output, do not report anything;
  * the daemon is shutting down (SIGTERM / CTRL-C): leave resumable files
    (partial downloads, rendered clips) in place for the next start.

Long operations call `cancel.raise_if_cancelled()` between steps; the
transfer client and the ffmpeg runner call it while they work.
"""

import threading
from dataclasses import dataclass, field

from shared.models import Lease

REASON_LEASE_LOST = "lease lost"
REASON_SHUTDOWN = "shutting down"


class TaskCancelledError(Exception):
    """The task must stop now; `reason` is REASON_LEASE_LOST or REASON_SHUTDOWN."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)

    @property
    def lease_lost(self) -> bool:
        return self.reason == REASON_LEASE_LOST


@dataclass
class ActiveLease:
    lease: Lease
    # Set by the heartbeat thread when the orchestrator answers `lost`.
    lost: threading.Event = field(default_factory=threading.Event)

    @property
    def lease_id(self) -> str:
        return self.lease.lease_id


class LeaseTracker:
    """Thread-safe set of the leases our heartbeats must keep renewing."""

    def __init__(self) -> None:
        self._leases: dict[str, ActiveLease] = {}
        self._lock = threading.Lock()

    def add(self, lease: Lease) -> ActiveLease:
        active = ActiveLease(lease)
        with self._lock:
            self._leases[lease.lease_id] = active
        return active

    def ids(self) -> list[str]:
        with self._lock:
            return list(self._leases)

    def mark_lost(self, lease_id: str) -> None:
        with self._lock:
            active = self._leases.get(lease_id)
        if active is not None:
            active.lost.set()

    def release(self, lease_id: str) -> None:
        """Stop renewing: the task ended (or we gave up and let it expire)."""
        with self._lock:
            self._leases.pop(lease_id, None)


class CancelToken:
    """What a task checks to know whether it should keep going."""

    def __init__(self, lease: ActiveLease | None, shutdown: threading.Event) -> None:
        self._lease = lease
        self._shutdown = shutdown

    def reason(self) -> str | None:
        """Why the task must stop, or None to keep going."""
        if self._shutdown.is_set():
            return REASON_SHUTDOWN
        if self._lease is not None and self._lease.lost.is_set():
            return REASON_LEASE_LOST
        return None

    def raise_if_cancelled(self) -> None:
        reason = self.reason()
        if reason is not None:
            raise TaskCancelledError(reason)

    def sleep(self, seconds: float) -> None:
        """Wait up to `seconds`, but raise TaskCancelledError as soon as asked to stop."""
        self._shutdown.wait(seconds)
        self.raise_if_cancelled()

    @property
    def shutting_down(self) -> bool:
        return self._shutdown.is_set()
