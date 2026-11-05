"""The localhost JSON API on 127.0.0.1:8700, for the tray icon and the status page.

LIVESTACK-38 item 9; documented in docs/api.md §7.3 and worker/README.md.

    GET  /api/status   → the status document below
    POST /api/pause    → "Pausar": finish the current task, take nothing new
    POST /api/resume   → "Retomar"
    POST /api/stop     → "Desligar o worker": stop the Windows service

Localhost only and no login, like the rest of the page. What keeps another
web site the volunteer visits from pressing these buttons:
  * the Host header must be localhost (setup_page checks it: no DNS tricks);
  * POSTs must send `Content-Type: application/json`. A cross-site page can
    only send that with fetch(), which makes the browser ask first (a CORS
    preflight) and we never say yes; plain HTML forms cannot send it;
  * a POST with an `Origin` header that is not this page is refused.
GET /api/status needs no extra check: a foreign page cannot read the answer
(no CORS headers), and it holds no secret (never the bearer token).
"""

import importlib.metadata
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from starlette.requests import Request
from starlette.responses import JSONResponse

from worker.config import WorkerConfig, load_config
from worker.pause import PauseSwitch
from worker.status import StatusBoard


def _iso(moment: datetime | None) -> str | None:
    return moment.isoformat().replace("+00:00", "Z") if moment else None


def worker_version() -> str:
    try:
        return importlib.metadata.version("worker")
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def build_status_document(config: WorkerConfig, status: StatusBoard, pause: PauseSwitch) -> dict:
    """Everything the tray and the status page need, as plain JSON.

    Facts only; turning them into a colour and a sentence is
    worker.tray.state's job (shared by the tray and the page).
    """
    facts = status.facts()
    values, _ = status.snapshot()
    # Before the first heartbeat the board knows no cap yet; worker.json does.
    cap_bytes = facts.cap_bytes if facts.cap_bytes is not None else config.storage_cap_bytes
    unreachable_seconds = None
    if facts.unreachable_since is not None:
        unreachable_seconds = (status.now() - facts.unreachable_since).total_seconds()
    return {
        "enrolled": config.is_enrolled,
        "enrollment_refused": facts.enrollment_refused,
        "connection_step_pending": config.connection_step_pending,
        "paused": pause.is_paused,
        "last_contact_at": _iso(facts.last_contact_at),
        # Seconds since the current outage began; null while connected.
        "unreachable_for_s": unreachable_seconds,
        "tasks": [
            {
                "kind": task.kind,
                "job_id": task.job_id,
                "prova": task.prova,
                "car_number": task.car_number,
                "percent": task.percent,
            }
            for task in facts.tasks
        ],
        "storage": {
            "used_bytes": facts.used_bytes,
            "cap_bytes": cap_bytes,
            "near_cap": facts.near_cap,
        },
        "held_raws": facts.held_raws,
        # Filled in by the clip archive (LIVESTACK-38 item 7); null until then.
        "clips_held": facts.clips_held,
        "clips_archived": facts.clips_archived,
        "details": {
            "worker_id": config.worker_id,
            "worker_name": config.worker_name,
            "role": str(config.role) if config.role else None,
            "orchestrator_url": config.resolved_orchestrator_url(),
            "tailnet_base_url": config.tailnet_base_url,
            "version": worker_version(),
            "last_error": facts.last_error,
            "notes": values,
        },
    }


def _post_is_allowed(request: Request, allowed_origins: set[str]) -> bool:
    content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if content_type != "application/json":
        return False
    origin = request.headers.get("origin")
    return origin is None or origin in allowed_origins


def build_api_endpoints(
    config_path: Path,
    status: StatusBoard,
    pause: PauseSwitch,
    stop_service: Callable[[], None],
    port: int,
    host_is_local: Callable[[Request], bool],
) -> dict[str, Callable]:
    """The four endpoint functions, keyed by path (setup_page mounts them)."""
    allowed_origins = {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}

    def refused() -> JSONResponse:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    async def get_status(request: Request) -> JSONResponse:
        if not host_is_local(request):
            return refused()
        return JSONResponse(build_status_document(load_config(config_path), status, pause))

    def action(do: Callable[[], None]):
        async def endpoint(request: Request) -> JSONResponse:
            if not host_is_local(request) or not _post_is_allowed(request, allowed_origins):
                return refused()
            do()
            return JSONResponse(build_status_document(load_config(config_path), status, pause))

        return endpoint

    return {
        "/api/status": get_status,
        "/api/pause": action(pause.pause),
        "/api/resume": action(pause.resume),
        "/api/stop": action(stop_service),
    }
