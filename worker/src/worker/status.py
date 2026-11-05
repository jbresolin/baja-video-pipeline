"""A small thread-safe notice board for the status page, its JSON API and the tray icon.

Components write what they are doing; the localhost page (and the tray
icon, through GET /api/status) reads a snapshot. Nothing here affects
behaviour; it only lets the volunteer see that the worker is alive and
what it is busy with.

Two kinds of entries:
  * free-form `values` ("orchestrator" → "connected", …), shown in the
    collapsed "Detalhes" section;
  * a few structured facts the novice view and the tray colour are built
    from: last contact with the server, since when it is unreachable,
    storage use, held raws, the running tasks and a refused enrollment.
"""

import threading
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime


@dataclass(frozen=True)
class RunningTask:
    """One task a stage is working on, as the status page shows it."""

    kind: str
    description: str
    job_id: str | None = None
    prova: str | None = None
    car_number: int | None = None
    # 0–100 when the task knows how far it is; None when it does not.
    percent: float | None = None


@dataclass(frozen=True)
class StatusFacts:
    """The structured part of the board (a copy; safe to read anywhere)."""

    last_contact_at: datetime | None = None
    # First failed contact of the current outage; None while connected.
    unreachable_since: datetime | None = None
    last_error: str | None = None
    used_bytes: int | None = None
    cap_bytes: int | None = None
    # The orchestrator said accepting_new_raws=false (near the storage cap).
    near_cap: bool = False
    held_raws: int | None = None
    # Clip archive (LIVESTACK-38 item 7): rendered clips kept in clips/, and
    # how many of them the master has archived. None until that code sets them.
    clips_held: int | None = None
    clips_archived: int | None = None
    enrollment_refused: bool = False
    tasks: tuple[RunningTask, ...] = field(default_factory=tuple)


def _utc_now() -> datetime:
    return datetime.now(UTC)


class StatusBoard:
    def __init__(self, clock=_utc_now) -> None:
        """`clock` returns the current UTC time (tests pass a fake one)."""
        self._lock = threading.Lock()
        self._clock = clock
        self._values: dict[str, str] = {}
        self._current_tasks: dict[str, RunningTask] = {}
        self._facts = StatusFacts()

    def now(self) -> datetime:
        return self._clock()

    def set(self, key: str, value: str) -> None:
        with self._lock:
            self._values[key] = value

    def set_now(self, key: str) -> None:
        """Store the current UTC time under `key` (e.g. "last heartbeat")."""
        self.set(key, self._clock().strftime("%Y-%m-%d %H:%M:%S UTC"))

    def task_started(self, stage: str, description: str, **details) -> None:
        """`details`: job_id, prova, car_number, percent (all optional)."""
        with self._lock:
            self._current_tasks[stage] = RunningTask(str(stage), description, **details)

    def task_finished(self, stage: str) -> None:
        with self._lock:
            self._current_tasks.pop(stage, None)

    def record_contact(self) -> None:
        """A heartbeat got an answer: connected now."""
        with self._lock:
            self._facts = replace(
                self._facts, last_contact_at=self._clock(), unreachable_since=None
            )

    def record_unreachable(self, error: str) -> None:
        """A heartbeat failed; the outage starts at the first failure."""
        with self._lock:
            since = self._facts.unreachable_since or self._clock()
            self._facts = replace(self._facts, unreachable_since=since, last_error=error)

    def set_storage(self, *, used_bytes: int, cap_bytes: int, held_raws: int) -> None:
        with self._lock:
            self._facts = replace(
                self._facts, used_bytes=used_bytes, cap_bytes=cap_bytes, held_raws=held_raws
            )

    def set_clips(self, *, held: int | None, archived: int | None) -> None:
        with self._lock:
            self._facts = replace(self._facts, clips_held=held, clips_archived=archived)

    def set_near_cap(self, near_cap: bool) -> None:
        with self._lock:
            self._facts = replace(self._facts, near_cap=near_cap)

    def set_enrollment_refused(self, refused: bool) -> None:
        with self._lock:
            self._facts = replace(self._facts, enrollment_refused=refused)

    def snapshot(self) -> tuple[dict[str, str], dict[str, str]]:
        """(general values, current task description per stage), as copies."""
        with self._lock:
            descriptions = {stage: task.description for stage, task in self._current_tasks.items()}
            return dict(self._values), descriptions

    def facts(self) -> StatusFacts:
        with self._lock:
            return replace(self._facts, tasks=tuple(self._current_tasks.values()))
