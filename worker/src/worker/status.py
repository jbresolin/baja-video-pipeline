"""A small thread-safe notice board for the setup/status page.

Components write what they are doing; the localhost page reads a snapshot.
Nothing here affects behaviour; it only lets the volunteer see that the
worker is alive and what it is busy with.
"""

import threading
from datetime import UTC, datetime


class StatusBoard:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._values: dict[str, str] = {}
        self._current_tasks: dict[str, str] = {}

    def set(self, key: str, value: str) -> None:
        with self._lock:
            self._values[key] = value

    def set_now(self, key: str) -> None:
        """Store the current UTC time under `key` (e.g. "last heartbeat")."""
        self.set(key, datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC"))

    def task_started(self, stage: str, description: str) -> None:
        with self._lock:
            self._current_tasks[stage] = description

    def task_finished(self, stage: str) -> None:
        with self._lock:
            self._current_tasks.pop(stage, None)

    def snapshot(self) -> tuple[dict[str, str], dict[str, str]]:
        """(general values, current task per stage), as copies."""
        with self._lock:
            return dict(self._values), dict(self._current_tasks)
