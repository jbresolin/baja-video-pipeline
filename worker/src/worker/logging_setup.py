"""One rotating, human-readable log file in <working folder>/logs/.

Every line carries the job id it is about (`job=<id>`, or `job=-` for lines
that are not about one job), so `grep job=abc123 worker.log` shows the
whole story of one job across threads.

The job id is kept in a context variable: `with job_context(job_id):`
around a task makes every log call inside it, in any module, carry the id.
Each thread starts with its own empty context, so stage threads do not see
each other's job.
"""

import logging
import logging.handlers
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

LOG_FILE_NAME = "worker.log"
LOG_FORMAT = "%(asctime)s %(levelname)-7s job=%(job_id)s [%(threadName)s] %(name)s: %(message)s"
MAX_LOG_FILE_BYTES = 10 * 1024 * 1024
LOG_BACKUP_COUNT = 5
NO_JOB = "-"

_current_job_id: ContextVar[str] = ContextVar("current_job_id", default=NO_JOB)


@contextmanager
def job_context(job_id: str) -> Iterator[None]:
    """Tag every log line written inside this block with `job_id`."""
    token = _current_job_id.set(job_id)
    try:
        yield
    finally:
        _current_job_id.reset(token)


class JobIdFilter(logging.Filter):
    """Adds `record.job_id` so the format string can print it."""

    def filter(self, record: logging.LogRecord) -> bool:
        # A caller may also pass extra={"job_id": ...} explicitly.
        if not hasattr(record, "job_id"):
            record.job_id = _current_job_id.get()
        return True


def configure_logging(log_folder: Path, *, also_to_console: bool = False) -> Path:
    """Send all log records to <log_folder>/worker.log (replacing earlier setup).

    Called once at start, and again after the setup page moved the working
    folder. Returns the log file path.
    """
    log_folder.mkdir(parents=True, exist_ok=True)
    log_path = log_folder / LOG_FILE_NAME
    root = logging.getLogger()
    for old_handler in list(root.handlers):
        root.removeHandler(old_handler)
        old_handler.close()

    formatter = logging.Formatter(LOG_FORMAT)
    file_handler = logging.handlers.RotatingFileHandler(
        log_path, maxBytes=MAX_LOG_FILE_BYTES, backupCount=LOG_BACKUP_COUNT, encoding="utf-8"
    )
    handlers: list[logging.Handler] = [file_handler]
    if also_to_console:
        handlers.append(logging.StreamHandler(sys.stderr))
    for handler in handlers:
        handler.setFormatter(formatter)
        handler.addFilter(JobIdFilter())
        root.addHandler(handler)
    root.setLevel(logging.INFO)

    # httpx logs every request at INFO; that would drown the useful lines.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    return log_path
