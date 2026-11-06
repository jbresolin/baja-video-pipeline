"""A small thread-safe notice board for the status page, its JSON API and the tray icon.

Components write what they are doing; the localhost page (and the tray
icon, through GET /api/status) reads a snapshot. Nothing here affects
behaviour; it only lets the volunteer see that the worker is alive and
what it is busy with.

Two kinds of entries:
  * free-form `values` ("orchestrator" → "connected", …), shown in the
    collapsed "Detalhes" section;
  * a few structured facts the novice view and the tray colour are built
    from: the startup phase, last contact with the server, since when it
    is unreachable, storage use, held raws, the running tasks, a refused
    enrollment, a missing tailnet and a revoked identity.
"""

import threading
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum


class Phase(StrEnum):
    """Where the worker is in its startup (worker.lifecycle), for the page and tray."""

    STARTING = "starting"
    # worker.json does not parse or lost a field: shown, waiting for a fix.
    CONFIG_PROBLEM = "config_problem"
    # Waiting for the enrollment token, or enrolling.
    SETUP = "setup"
    # A studio that has not chosen the folder OBS plays from yet.
    STUDIO_FOLDER = "studio_folder"
    # Enrolled, but the saved tailnet address is not on this PC (yet).
    WAITING_FOR_TAILNET = "waiting_for_tailnet"
    RUNNING = "running"


class TailnetProblemKind(StrEnum):
    # `tailscale status` does not answer: not installed, or still starting.
    TAILSCALE_NOT_ANSWERING = "tailscale_not_answering"
    # Tailscale answers but is logged out or stopped (e.g. the device was
    # removed from the tailnet in the admin console).
    NOT_CONNECTED = "not_connected"
    # Tailscale is up with another address: the PC is on another tailnet,
    # or it re-joined as a new device. The saved address is not ours.
    ADDRESS_NOT_ON_THIS_PC = "address_not_on_this_pc"


@dataclass(frozen=True)
class TailnetProblem:
    """Why the worker is waiting for the tailnet (worker.tailnet_wait)."""

    kind: TailnetProblemKind
    # The address worker.json says we have.
    saved_address: str
    # The address Tailscale says we have now, when it says one.
    current_address: str | None = None


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
    phase: Phase = Phase.STARTING
    tailnet_problem: TailnetProblem | None = None
    # The orchestrator answered 401 to our bearer token: this worker was
    # revoked (or its identity is not known there). "Registrar de novo".
    identity_rejected: bool = False


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

    def set_phase(self, phase: Phase) -> None:
        with self._lock:
            self._facts = replace(self._facts, phase=phase)

    def set_tailnet_problem(self, problem: TailnetProblem | None) -> None:
        with self._lock:
            self._facts = replace(self._facts, tailnet_problem=problem)

    def set_identity_rejected(self, rejected: bool) -> None:
        with self._lock:
            self._facts = replace(self._facts, identity_rejected=rejected)

    def forget_identity_facts(self) -> None:
        """After "Registrar de novo": what we knew about the old identity
        (contact, refusal, storage numbers) no longer describes this PC."""
        with self._lock:
            self._facts = replace(
                self._facts,
                last_contact_at=None,
                unreachable_since=None,
                last_error=None,
                identity_rejected=False,
                enrollment_refused=False,
                near_cap=False,
                tailnet_problem=None,
            )
            self._values.clear()

    def snapshot(self) -> tuple[dict[str, str], dict[str, str]]:
        """(general values, current task description per stage), as copies."""
        with self._lock:
            descriptions = {stage: task.description for stage, task in self._current_tasks.items()}
            return dict(self._values), descriptions

    def facts(self) -> StatusFacts:
        with self._lock:
            return replace(self._facts, tasks=tuple(self._current_tasks.values()))
