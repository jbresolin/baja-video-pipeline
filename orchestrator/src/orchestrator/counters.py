"""In-process event counters for GET /metrics (Prometheus counters).

They count what happens while this process runs: task offers, claims and
failure reports. They start at zero on every restart, which Prometheus
handles by design (rate() and increase() detect counter resets). Anything
that must survive a restart is read from the DB instead (job states,
workers), see domain/metrics.py.

Request handlers run in a thread pool, so every update takes a lock.
"""

import threading
from collections import Counter

from shared.models import FailureScope, TaskKind


class TaskCounters:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._offers: Counter[TaskKind] = Counter()
        self._claims: Counter[TaskKind] = Counter()
        self._failures: Counter[tuple[TaskKind, FailureScope]] = Counter()

    def count_offer(self, kind: TaskKind) -> None:
        """/workers/{id}/next offered a task of this kind."""
        with self._lock:
            self._offers[kind] += 1

    def count_claim(self, kind: TaskKind) -> None:
        """/jobs/{id}/claim created a new lease of this kind."""
        with self._lock:
            self._claims[kind] += 1

    def count_failure(self, kind: TaskKind, scope: FailureScope) -> None:
        """/jobs/{id}/failed was accepted for a task of this kind."""
        with self._lock:
            self._failures[(kind, scope)] += 1

    def snapshot(self) -> tuple[dict, dict, dict]:
        """Copies of (offers, claims, failures), safe to read without the lock."""
        with self._lock:
            return dict(self._offers), dict(self._claims), dict(self._failures)
