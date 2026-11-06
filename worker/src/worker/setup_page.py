"""The only UI a volunteer sees: http://127.0.0.1:8700 (design §9).

Before enrollment: a form with the enrollment token, the working folder
and the storage cap. Submitting it writes worker.json; the daemon notices
and enrolls. Afterwards the same page shows the novice status page
(worker.status_page, pt-BR), refreshed every 5 seconds. The same server
answers the localhost JSON API for the tray icon (worker.local_api).

It listens on localhost only. Requests whose Host header is not localhost
are refused, so a web page the volunteer visits cannot reach it through a
DNS trick, and the form is accepted only while not yet enrolled.
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

from worker.config import GIGABYTE, load_config, save_config
from worker.display_name import display_name_change, display_name_input
from worker.local_api import build_api_endpoints, build_status_document
from worker.pause import PauseSwitch
from worker.status import StatusBoard
from worker.status_page import STATUS_STYLE, render_status_body

PAGE_STYLE = (
    "body{font-family:system-ui,sans-serif;max-width:40em;margin:2em auto;padding:0 1em}"
    "label{display:block;margin-top:1em}input{width:100%;padding:.4em;font-size:1em}"
    "button{margin-top:1.5em;padding:.6em 1.2em;font-size:1em}"
    "table{border-collapse:collapse}td{padding:.2em 1em .2em 0}.err{color:#b00}"
)


def _page(title: str, body: str, refresh: bool = False, lang: str = "en") -> HTMLResponse:
    meta = '<meta http-equiv="refresh" content="5">' if refresh else ""
    return HTMLResponse(
        f"<!doctype html><html lang='{lang}'><head><meta charset='utf-8'>{meta}"
        f"<title>{title}</title><style>{PAGE_STYLE}{STATUS_STYLE}</style></head>"
        f"<body><h1>{title}</h1>{body}</body></html>"
    )


def build_setup_app(
    config_path: Path,
    status: StatusBoard,
    setup_submitted: threading.Event,
    port: int,
    *,
    pause: PauseSwitch | None = None,
    stop_service: Callable[[], None] | None = None,
) -> Starlette:
    """`pause` is shared with the daemon (a fresh switch on worker.json if
    not given); `stop_service` is what "Desligar o worker" does (default:
    nothing, so tests and plain `python -m worker` runs are safe)."""
    pause = pause or PauseSwitch(config_path)
    stop_service = stop_service or (lambda: None)
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}", "127.0.0.1", "localhost"}

    def host_is_local(request: Request) -> bool:
        return request.headers.get("host", "") in allowed_hosts

    async def show(request: Request) -> Response:
        if not host_is_local(request):
            return PlainTextResponse("forbidden", status_code=403)
        config = load_config(config_path)
        if config.is_enrolled:
            return _status_page(config, status, pause)
        return _form_page(config, config_path, status)

    async def submit(request: Request) -> Response:
        if not host_is_local(request):
            return PlainTextResponse("forbidden", status_code=403)
        config = load_config(config_path)
        if config.is_enrolled:
            return PlainTextResponse("already set up", status_code=409)
        fields = {key: values[0].strip() for key, values in parse_qs(
            (await request.body()).decode("utf-8")).items()}  # fmt: skip
        token = fields.get("token", "")
        folder = Path(fields.get("folder", ""))
        try:
            cap_bytes = int(float(fields.get("cap_gb", "0")) * GIGABYTE)
        except ValueError:
            cap_bytes = 0
        if not token or not folder.is_absolute() or cap_bytes <= 0:
            status.set("setup", "please fill in all three fields (the folder as a full path)")
            return RedirectResponse("/", status_code=303)
        updated = config.model_copy(
            update={
                "enrollment_token": token,
                "working_folder": folder,
                "storage_cap_bytes": cap_bytes,
                **display_name_change(fields),
            }
        )
        save_config(config_path, updated)
        setup_submitted.set()
        return RedirectResponse("/", status_code=303)

    api = build_api_endpoints(config_path, status, pause, stop_service, port, host_is_local)
    return Starlette(
        routes=[
            Route("/", show, methods=["GET"]),
            Route("/setup", submit, methods=["POST"]),
            Route("/api/status", api["/api/status"], methods=["GET"]),
            Route("/api/pause", api["/api/pause"], methods=["POST"]),
            Route("/api/resume", api["/api/resume"], methods=["POST"]),
            Route("/api/stop", api["/api/stop"], methods=["POST"]),
        ]
    )


def _form_page(config, config_path: Path, status: StatusBoard) -> HTMLResponse:
    values, _ = status.snapshot()
    message = values.get("setup", "")
    folder = config.resolved_working_folder(config_path)
    cap_gb = config.storage_cap_bytes / GIGABYTE
    body = (
        f"<p class='err'>{html.escape(message)}</p>"
        "<form method='post' action='/setup'>"
        "<label>Enrollment token (from the Baja team)"
        "<input name='token' required autocomplete='off'></label>"
        f"{display_name_input(config)}"
        "<label>Working folder (raws and clips are stored here)"
        f"<input name='folder' required value='{html.escape(str(folder))}'></label>"
        "<label>Storage cap in GB (the worker never uses more)"
        f"<input name='cap_gb' type='number' min='1' step='1' required value='{cap_gb:.0f}'>"
        "</label><button type='submit'>Save and connect</button></form>"
    )
    return _page("Baja worker setup", body, refresh=bool(message))


def _status_page(config, status: StatusBoard, pause: PauseSwitch) -> HTMLResponse:
    document = build_status_document(config, status, pause)
    body = render_status_body(document, status.now())
    return _page("Baja Worker", body, refresh=True, lang="pt-BR")
