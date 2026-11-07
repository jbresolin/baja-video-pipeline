"""The localhost JSON API on 127.0.0.1:8700, for the tray icon and the status page.

LIVESTACK-38 item 9; documented in docs/api.md §7.3 and worker/README.md.

    GET  /api/status       → the status document below
    GET  /api/page         → the page's live parts as HTML, for refreshing
                             them in place (worker.setup_page)
    POST /api/pause        → "Pausar": finish the current task, take nothing new
    POST /api/resume       → "Retomar"
    POST /api/stop         → "Desligar o worker": stop the Windows service
    POST /api/re-register  → "Registrar de novo": forget this PC's identity
                             and go back to the token form (worker.lifecycle)

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

import importlib
import importlib.metadata
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from starlette.requests import Request
from starlette.responses import JSONResponse

from shared.models import WorkerRole
from worker.config import WorkerConfig
from worker.config_problems import ConfigProblem, ConfigReading, read_config
from worker.pause import PauseSwitch
from worker.status import StatusBoard, TailnetProblem
from worker.studio.clip_folder import playlist_path


def _iso(moment: datetime | None) -> str | None:
    return moment.isoformat().replace("+00:00", "Z") if moment else None


# Written by installer/build.py into every Windows build: the installer's
# version ("0.3.0"). It does not exist when running from the source tree.
INSTALLER_BUILD_INFO_MODULE = "baja_installer._build_info"


def worker_version() -> str:
    """The version a volunteer installed ("0.3.0"), shown on the :8700 page.

    The Python package's own version (worker/pyproject.toml) is never
    bumped, so every installer showed "0.1.0" there; the build's stamp is
    the real one. From source: the package version, else "unknown".
    """
    try:
        build_info = importlib.import_module(INSTALLER_BUILD_INFO_MODULE)
        return build_info.VERSION
    except ImportError:
        pass
    try:
        return importlib.metadata.version("worker")
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def build_status_document(
    config: WorkerConfig | None,
    status: StatusBoard,
    pause: PauseSwitch,
    *,
    problem: ConfigProblem | None = None,
) -> dict:
    """Everything the tray and the status page need, as plain JSON.

    Facts only; turning them into a colour and a sentence is
    worker.tray.state's job (shared by the tray and the page). `config` is
    None when worker.json does not parse; `problem` says what is wrong
    with it (worker.config_problems).
    """
    known = config if config is not None else WorkerConfig()
    facts = status.facts()
    values, _ = status.snapshot()
    # Before the first heartbeat the board knows no cap yet; worker.json does.
    cap_bytes = facts.cap_bytes if facts.cap_bytes is not None else known.storage_cap_bytes
    unreachable_seconds = None
    if facts.unreachable_since is not None:
        unreachable_seconds = (status.now() - facts.unreachable_since).total_seconds()
    can_re_register = config is not None and (
        config.is_enrolled or (problem is not None and problem.fixable_by_re_registering)
    )
    return {
        "phase": str(facts.phase),
        "enrolled": config is not None and config.is_enrolled and problem is None,
        "enrollment_refused": facts.enrollment_refused,
        "connection_step_pending": problem is None and known.connection_step_pending,
        # worker.json does not parse or lost a field (red; the page explains).
        "config_problem": problem.as_json() if problem is not None else None,
        # Waiting for the tailnet address (yellow; worker.tailnet_wait).
        "tailnet_problem": _tailnet_problem_json(facts.tailnet_problem),
        # The orchestrator refuses our token: revoked (red; "Registrar de novo").
        "identity_rejected": facts.identity_rejected,
        "can_re_register": can_re_register,
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
        "studio": _studio_json(known, values),
        "details": {
            "worker_id": known.worker_id,
            "worker_name": known.worker_name,
            "role": str(known.role) if known.role else None,
            "orchestrator_url": known.resolved_orchestrator_url(),
            "tailnet_base_url": known.tailnet_base_url,
            "tailnet_address": known.tailnet_address,
            # "Usar a rede atual" (item 4): on the PC's own tailnet.
            "shared_tailnet": known.shared_tailnet,
            "version": worker_version(),
            "last_error": facts.last_error,
            "notes": values,
        },
    }


def status_document_for(config_path: Path, status: StatusBoard, pause: PauseSwitch) -> dict:
    reading: ConfigReading = read_config(config_path)
    return build_status_document(reading.config, status, pause, problem=reading.problem)


def _tailnet_problem_json(problem: TailnetProblem | None) -> dict | None:
    if problem is None:
        return None
    return {
        "kind": str(problem.kind),
        "saved_address": problem.saved_address,
        "current_address": problem.current_address,
    }


def _studio_json(config: WorkerConfig, values: dict[str, str]) -> dict | None:
    """Studio only, once its clip folder is chosen: where OBS reads from."""
    if config.studio_clip_root is None or config.role != WorkerRole.STUDIO:
        return None
    folder_note = values.get("studio folder")
    return {
        "clip_root": str(config.studio_clip_root),
        "playlist_path": str(playlist_path(config.studio_clip_root)),
        # A pt-BR sentence when creating the folder or the read grant failed.
        "folder_problem": folder_note if folder_note and folder_note != "ok" else None,
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
    re_register: Callable[[], bool],
    page_update: Callable[[], dict],
) -> dict[str, Callable]:
    """The endpoint functions, keyed by path (setup_page mounts them).

    `page_update()` renders the page's live parts (worker.setup_page);
    `re_register()` is WorkerLifecycle.re_register.
    """
    allowed_origins = {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}

    def refused() -> JSONResponse:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    async def get_status(request: Request) -> JSONResponse:
        if not host_is_local(request):
            return refused()
        return JSONResponse(status_document_for(config_path, status, pause))

    async def get_page(request: Request) -> JSONResponse:
        if not host_is_local(request):
            return refused()
        return JSONResponse(page_update())

    def action(do: Callable[[], object]):
        async def endpoint(request: Request) -> JSONResponse:
            if not host_is_local(request) or not _post_is_allowed(request, allowed_origins):
                return refused()
            do()
            return JSONResponse(status_document_for(config_path, status, pause))

        return endpoint

    return {
        "/api/status": get_status,
        "/api/page": get_page,
        "/api/pause": action(pause.pause),
        "/api/resume": action(pause.resume),
        "/api/stop": action(stop_service),
        "/api/re-register": action(re_register),
    }
