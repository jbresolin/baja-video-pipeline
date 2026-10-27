"""GET /auth/file: may this worker download this inbox/outbox file? (docs/api.md §2.1)

Caddy asks before serving /inbox/* and /outbox/* on the tailnet listener.
The rules are deliberately narrow:

    /inbox/<job_id>.<ext>  only while the caller holds a download_raw lease on that job
    /outbox/<job_id>.mp4   studio only
    anything else          403 (other paths, dot-files, "..", %-escapes, methods other
                           than GET/HEAD)
"""

import re
import sqlite3

from orchestrator.context import OrchestratorContext
from orchestrator.errors import forbidden
from orchestrator.repositories import leases as leases_repo
from shared.models import TaskKind, WorkerRole

# Job ids are UUID4 hex. Nothing else can match, so "..", dot-files and
# escapes (%2e) are refused simply by not matching.
INBOX_PATH = re.compile(r"^/inbox/([0-9a-f]{32})\.[a-z0-9]{1,5}$")
OUTBOX_PATH = re.compile(r"^/outbox/([0-9a-f]{32})\.mp4$")
ALLOWED_METHODS = frozenset({"GET", "HEAD"})


def authorize_file_request(
    context: OrchestratorContext,
    worker_row: sqlite3.Row,
    forwarded_uri: str,
    forwarded_method: str | None,
) -> None:
    """Return normally to allow; raise a 403 ApiError to refuse."""
    method = (forwarded_method or "GET").upper()
    if method not in ALLOWED_METHODS:
        raise forbidden(f"{method} is not allowed on files")
    # Caddy forwards the request URI, which may carry a query string.
    path = forwarded_uri.split("?", 1)[0]

    inbox_match = INBOX_PATH.match(path)
    if inbox_match:
        job_id = inbox_match.group(1)
        now = context.clock.now()
        with context.database.transaction() as connection:
            leases = leases_repo.list_leases_for_job(connection, job_id, TaskKind.DOWNLOAD_RAW)
        if any(lease.worker_id == worker_row["id"] and lease.expires_at > now for lease in leases):
            return
        raise forbidden("no download_raw lease on this job")

    if OUTBOX_PATH.match(path):
        if worker_row["role"] == WorkerRole.STUDIO:
            return
        raise forbidden("only the studio may download outbox clips")

    raise forbidden("this path is not served to workers")
