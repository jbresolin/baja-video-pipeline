"""The only UI a volunteer sees: http://127.0.0.1:8700 (design §9).

The page always comes up, before the worker does anything else
(worker.__main__), and shows one of:
  * worker.json is broken: the file, line, column and a hint (item 3);
  * before enrollment: the token form (token, working folder, storage cap);
  * a studio right after enrollment: the clip folder form (item 5);
  * afterwards: the pt-BR status page (worker.status_page), including
    "not on the Baja tailnet" (item 1) and "Registrar de novo" (item 2).

It never reloads itself: page.js refreshes only the live region in place
and leaves a form alone while the volunteer uses it (item 14). The same
server answers the localhost JSON API for the tray icon (worker.local_api).

It listens on localhost only. Requests whose Host header is not localhost
are refused, so a web page the volunteer visits cannot reach it through a
DNS trick, and each form is accepted only in its own phase.
"""

import html
import threading
from collections.abc import Callable
from pathlib import Path
from urllib.parse import parse_qs

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from starlette.routing import Route

from shared.models import WorkerRole
from worker.config import GIGABYTE, save_config
from worker.config_problems import read_config
from worker.display_name import display_name_change
from worker.local_api import build_api_endpoints, build_status_document
from worker.page_assets import page_script
from worker.page_views import (
    STATUS_TITLE,
    PageParts,
    config_problem_parts,
    studio_folder_parts,
    token_form_parts,
)
from worker.pause import PauseSwitch
from worker.status import StatusBoard
from worker.status_page import STATUS_STYLE, render_status_body

PAGE_STYLE = (
    "body{font-family:system-ui,sans-serif;max-width:40em;margin:2em auto;padding:0 1em}"
    "label{display:block;margin-top:1em}input{width:100%;padding:.4em;font-size:1em}"
    "button{margin-top:1.5em;padding:.6em 1.2em;font-size:1em}"
    "table{border-collapse:collapse}td{padding:.2em 1em .2em 0}.err{color:#b00}"
    "pre.line{background:#222;color:#eee;padding:.6em;overflow-x:auto}"
)
# Status keys (StatusBoard values) for the messages the two forms leave.
SETUP_MESSAGE_KEY = "setup"
STUDIO_FOLDER_MESSAGE_KEY = "studio folder form"
FILL_IN_ALL_FIELDS_MESSAGE = (
    "Preencha os três campos (a pasta com o caminho completo, como D:\\BajaWorker)."
)
FULL_PATH_NEEDED_MESSAGE = (
    "Escreva o caminho completo da pasta, começando pela letra do disco (C:\\BajaClips)."
)


def render_page(parts: PageParts) -> str:
    """The whole document: live region, form region and the script."""
    return (
        "<!doctype html><html lang='pt-BR'><head><meta charset='utf-8'>"
        f"<title>{html.escape(parts.title)}</title><style>{PAGE_STYLE}{STATUS_STYLE}</style>"
        f"</head><body><div id='live'>{parts.live_html}</div>"
        f"<div id='forms' data-form='{parts.form_key}' data-edited='no'>{parts.form_html}</div>"
        f"<script>{page_script()}</script></body></html>"
    )


def page_parts(config_path: Path, status: StatusBoard, pause: PauseSwitch) -> PageParts:
    """Which view to show right now, from worker.json and the status board."""
    reading = read_config(config_path)
    document = build_status_document(reading.config, status, pause, problem=reading.problem)
    values, _ = status.snapshot()
    if reading.problem is not None:
        return config_problem_parts(reading.problem, document)
    config = reading.config
    assert config is not None
    if not config.is_enrolled:
        return token_form_parts(config, config_path, values.get(SETUP_MESSAGE_KEY, ""))
    if config.role == WorkerRole.STUDIO and config.studio_clip_root is None:
        message = values.get(STUDIO_FOLDER_MESSAGE_KEY, "")
        return studio_folder_parts(config, config_path, message)
    return PageParts(STATUS_TITLE, render_status_body(document, status.now()))


def _form_fields(body: bytes) -> dict[str, str]:
    return {key: values[0].strip() for key, values in parse_qs(body.decode("utf-8")).items()}


def build_setup_app(
    config_path: Path,
    status: StatusBoard,
    setup_submitted: threading.Event,
    port: int,
    *,
    pause: PauseSwitch | None = None,
    stop_service: Callable[[], None] | None = None,
    re_register: Callable[[], bool] | None = None,
) -> Starlette:
    """`pause` is shared with the daemon (a fresh switch on worker.json if
    not given); `stop_service` is what "Desligar o worker" does and
    `re_register` what "Registrar de novo" does (WorkerLifecycle.re_register).
    Their defaults do nothing, so tests and plain runs are safe."""
    pause = pause or PauseSwitch(config_path)
    stop_service = stop_service or (lambda: None)
    re_register = re_register or (lambda: False)
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}", "127.0.0.1", "localhost"}

    def host_is_local(request: Request) -> bool:
        return request.headers.get("host", "") in allowed_hosts

    def current_parts() -> PageParts:
        return page_parts(config_path, status, pause)

    async def show(request: Request) -> Response:
        if not host_is_local(request):
            return PlainTextResponse("forbidden", status_code=403)
        return HTMLResponse(render_page(current_parts()))

    async def submit_token(request: Request) -> Response:
        if not host_is_local(request):
            return PlainTextResponse("forbidden", status_code=403)
        reading = read_config(config_path)
        if reading.config is None or reading.problem is not None:
            return RedirectResponse("/", status_code=303)  # the page shows the problem
        config = reading.config
        if config.is_enrolled:
            return PlainTextResponse("already set up", status_code=409)
        fields = _form_fields(await request.body())
        token = fields.get("token", "")
        folder = Path(fields.get("folder", ""))
        try:
            cap_bytes = int(float(fields.get("cap_gb", "0")) * GIGABYTE)
        except ValueError:
            cap_bytes = 0
        if not token or not folder.is_absolute() or cap_bytes <= 0:
            status.set(SETUP_MESSAGE_KEY, FILL_IN_ALL_FIELDS_MESSAGE)
            return RedirectResponse("/", status_code=303)
        updated = config.model_copy(
            update={
                "enrollment_token": token,
                "working_folder": folder,
                "storage_cap_bytes": cap_bytes,
                # LIVESTACK-42 item 7: the name the team sees, if the volunteer typed one.
                **display_name_change(fields),
            }
        )
        save_config(config_path, updated)
        setup_submitted.set()
        return RedirectResponse("/", status_code=303)

    async def submit_studio_folder(request: Request) -> Response:
        """Item 5: a studio's clip folder; the lifecycle creates it and opens it to OBS."""
        if not host_is_local(request):
            return PlainTextResponse("forbidden", status_code=403)
        reading = read_config(config_path)
        config = reading.config
        if config is None or reading.problem is not None:
            return RedirectResponse("/", status_code=303)
        if config.role != WorkerRole.STUDIO or config.studio_clip_root is not None:
            return PlainTextResponse("not asking for a clip folder now", status_code=409)
        folder = Path(_form_fields(await request.body()).get("clip_folder", ""))
        if not folder.is_absolute():
            status.set(STUDIO_FOLDER_MESSAGE_KEY, FULL_PATH_NEEDED_MESSAGE)
            return RedirectResponse("/", status_code=303)
        save_config(config_path, config.model_copy(update={"studio_clip_root": folder}))
        status.set(STUDIO_FOLDER_MESSAGE_KEY, "")
        setup_submitted.set()
        return RedirectResponse("/", status_code=303)

    api = build_api_endpoints(
        config_path,
        status,
        pause,
        stop_service,
        port,
        host_is_local,
        re_register=re_register,
        page_update=lambda: current_parts().as_json(),
    )
    return Starlette(
        routes=[
            Route("/", show, methods=["GET"]),
            Route("/setup", submit_token, methods=["POST"]),
            Route("/setup/studio-folder", submit_studio_folder, methods=["POST"]),
            Route("/api/status", api["/api/status"], methods=["GET"]),
            Route("/api/page", api["/api/page"], methods=["GET"]),
            Route("/api/pause", api["/api/pause"], methods=["POST"]),
            Route("/api/resume", api["/api/resume"], methods=["POST"]),
            Route("/api/stop", api["/api/stop"], methods=["POST"]),
            Route("/api/re-register", api["/api/re-register"], methods=["POST"]),
        ]
    )
