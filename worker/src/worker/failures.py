"""How a task ends when it does not succeed, and whose fault it is (design §17).

A task handler either returns (success) or raises. The stage runner turns
what it raised into one of three endings:

  * TaskCancelledError (worker.leases): lease lost or shutdown. Just stop.
  * TaskAbandonedError: give up quietly and let the lease expire, so the
    task is offered again later. Used when we cannot even reach the
    orchestrator to report, and for the cases in
    NETWORK_FAILURES_LEFT_TO_EXPIRE below.
  * anything else: POST /jobs/{id}/failed, with a `scope` that says whose
    fault it is, decided only by `failure_scope_for` below:

      scope "job"     the input itself is bad; the job fails at once.
                      - SourceHashMismatchError: every source sent bytes
                        whose SHA-256 differs from the offer's (the copy at
                        the source is wrong);
                      - UndecodableInputError: ffprobe cannot read the raw;
                      - InvalidTaskError: the offer itself is unusable
                        (e.g. an unsafe clip path).
      scope "worker"  this worker could not do it; the task is re-offered
                      to another worker.
                      - MediaToolError: local ffmpeg crash / non-zero exit;
                      - TransferGaveUpError: network gave up after retries;
                      - OSError: disk full, permissions, a vanished file;
                      - TaskFailedError: other local problems (raw missing);
                      - any other exception: a bug in the worker.
"""

import logging
from pathlib import Path

from shared.media_probe import MediaInfo
from shared.models import FailureScope, TaskKind, WorkerRole
from worker.media_tools import MediaToolError, MediaTools

logger = logging.getLogger(__name__)


class TaskFailedError(Exception):
    """Report POST /jobs/{id}/failed. Scope "worker" unless a subclass says otherwise."""

    def __init__(self, message: str, exit_code: int | None = None) -> None:
        self.message = message
        self.exit_code = exit_code
        super().__init__(message)


class SourceHashMismatchError(TaskFailedError):
    """Every source sent a file with the wrong SHA-256: the source copy is bad."""


class UndecodableInputError(TaskFailedError):
    """ffprobe cannot read the raw (or finds no video in it)."""


class InvalidTaskError(TaskFailedError):
    """The offer cannot be carried out by anyone (e.g. an unsafe clip path)."""


class TransferGaveUpError(TaskFailedError):
    """A download or upload failed even after its retries (network trouble)."""


class TaskAbandonedError(Exception):
    """Give up for now without reporting; the lease expires and the task is re-offered."""


# The one table that decides the scope. First matching type wins, so the
# specific "job" errors come before their TaskFailedError parent and the
# final `Exception` row catches bugs.
SCOPE_BY_ERROR_TYPE: tuple[tuple[type[BaseException], FailureScope], ...] = (
    (SourceHashMismatchError, FailureScope.JOB),
    (UndecodableInputError, FailureScope.JOB),
    (InvalidTaskError, FailureScope.JOB),
    (TransferGaveUpError, FailureScope.WORKER),
    (MediaToolError, FailureScope.WORKER),
    (OSError, FailureScope.WORKER),
    (TaskFailedError, FailureScope.WORKER),
    (Exception, FailureScope.WORKER),
)

# Tasks only this worker can do: the master must end up holding every raw
# (design §4) and there is one studio. A "worker" failure report would
# exclude it from that task on that job until a reviewer acts, stranding
# the raw or the clip. So for network trouble in these tasks we let the
# lease expire instead; the orchestrator offers the same task again later.
NETWORK_FAILURES_LEFT_TO_EXPIRE = frozenset(
    {(WorkerRole.MASTER, TaskKind.DOWNLOAD_RAW), (WorkerRole.STUDIO, TaskKind.DELIVER)}
)

# Errors whose message is written for a person already (ffmpeg's last lines,
# "every source sent ..."); others get a prefix saying what happened.
_ERRORS_WITH_READABLE_MESSAGES = (TaskFailedError, MediaToolError)


def failure_scope_for(error: BaseException) -> FailureScope:
    for error_type, scope in SCOPE_BY_ERROR_TYPE:
        if isinstance(error, error_type):
            return scope
    return FailureScope.WORKER  # not an Exception at all (never expected)


def should_report_failure(error: BaseException, role: WorkerRole | None, kind: TaskKind) -> bool:
    """False when the failure must not be reported (the lease is left to expire)."""
    is_network_failure = isinstance(error, TransferGaveUpError)
    only_we_can_do_it = (role, kind) in NETWORK_FAILURES_LEFT_TO_EXPIRE
    return not (is_network_failure and only_we_can_do_it)


def failure_message_for(error: BaseException, kind: TaskKind) -> str:
    """The text for FailedRequest.message (shown to the reviewer)."""
    if isinstance(error, _ERRORS_WITH_READABLE_MESSAGES):
        return error.message or f"{kind} failed"  # the API refuses an empty message
    if isinstance(error, OSError):
        return f"local disk/file error during {kind}: {error}"
    return f"internal worker error during {kind}: {error!r}"


def failure_exit_code_for(error: BaseException) -> int | None:
    return getattr(error, "exit_code", None)


def is_expected_failure(error: BaseException) -> bool:
    """True for failures we raise on purpose; False means a bug (log the traceback)."""
    return isinstance(error, (*_ERRORS_WITH_READABLE_MESSAGES, OSError))


def probe_input(media: MediaTools, raw_path: Path) -> MediaInfo:
    """ffprobe the raw; when ffprobe cannot read it, the input itself is bad.

    Use this for the raw only. Probing our own output (the proxy) failing
    is a different matter and must not blame the job.
    """
    try:
        return media.probe(raw_path)
    except MediaToolError as error:
        message = f"the raw is not a readable video: {error.message}"
        raise UndecodableInputError(message, error.exit_code) from error
