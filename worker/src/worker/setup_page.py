"""The only UI a volunteer sees: http://127.0.0.1:8700 (design §9).

Before enrollment: a form with the enrollment token, the working folder
and the storage cap. Submitting it writes worker.json; the daemon notices
and enrolls. Afterwards the same page shows a status summary, refreshed
every 5 seconds.

It listens on localhost only. Requests whose Host header is not localhost
are refused, so a web page the volunteer visits cannot reach it through a
DNS trick, and the form is accepted only while not yet enrolled.
"""

import html
import threading
from pathlib import Path
from urllib.parse import parse_qs

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from starlette.routing import Route

from worker.config import GIGABYTE, load_config, save_config
from worker.status import StatusBoard

PAGE_STYLE = (
    "body{font-family:system-ui,sans-serif;max-width:40em;margin:2em auto;padding:0 1em}"
    "label{display:block;margin-top:1em}input{width:100%;padding:.4em;font-size:1em}"
    "button{margin-top:1.5em;padding:.6em 1.2em;font-size:1em}"
    "table{border-collapse:collapse}td{padding:.2em 1em .2em 0}.err{color:#b00}"
)


def _page(title: str, body: str, refresh: bool = False) -> HTMLResponse:
    meta = '<meta http-equiv="refresh" content="5">' if refresh else ""
    return HTMLResponse(
        f"<!doctype html><html><head><meta charset='utf-8'>{meta}<title>{title}</title>"
        f"<style>{PAGE_STYLE}</style></head><body><h1>{title}</h1>{body}</body></html>"
    )


def build_setup_app(
    config_path: Path, status: StatusBoard, setup_submitted: threading.Event, port: int
) -> Starlette:
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}", "127.0.0.1", "localhost"}

    def host_is_local(request: Request) -> bool:
        return request.headers.get("host", "") in allowed_hosts

    async def show(request: Request) -> Response:
        if not host_is_local(request):
            return PlainTextResponse("forbidden", status_code=403)
        config = load_config(config_path)
        if config.is_enrolled:
            return _status_page(config, status)
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
            }
        )
        save_config(config_path, updated)
        setup_submitted.set()
        return RedirectResponse("/", status_code=303)

    return Starlette(
        routes=[
            Route("/", show, methods=["GET"]),
            Route("/setup", submit, methods=["POST"]),
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
        "<label>Working folder (raws and clips are stored here)"
        f"<input name='folder' required value='{html.escape(str(folder))}'></label>"
        "<label>Storage cap in GB (the worker never uses more)"
        f"<input name='cap_gb' type='number' min='1' step='1' required value='{cap_gb:.0f}'>"
        "</label><button type='submit'>Save and connect</button></form>"
    )
    return _page("Baja worker setup", body, refresh=bool(message))


def _status_page(config, status: StatusBoard) -> HTMLResponse:
    values, tasks = status.snapshot()
    rows = {
        "Worker": f"{config.worker_name} ({config.worker_id})",
        "Role": str(config.role),
        **values,
    }
    for stage, description in sorted(tasks.items()):
        rows[f"Busy: {stage}"] = description
    if not tasks:
        rows["Busy"] = "idle"
    table = "".join(
        f"<tr><td>{html.escape(key)}</td><td>{html.escape(value)}</td></tr>"
        for key, value in rows.items()
    )
    return _page("Baja worker", f"<table>{table}</table>", refresh=True)
