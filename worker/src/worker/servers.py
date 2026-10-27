"""The peer file server app (port 8710) and a helper to run any app in a thread.

Peer file server (docs/api.md §7.2), master and worker roles:
    GET /health            {"status": "ok", "worker_id": …, "role": …}
    GET /raw/{job_id}      the held raw, with Range (206) support; 404 if not held

Only complete raws are served: partial downloads are hidden dot-files that
find_raw() never returns. No app-level auth in v1 (tailnet ACL, design §15).
"""

import logging
import threading

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, Response
from starlette.routing import Route

from shared.models import ErrorCode
from worker.storage import Storage
from worker.studio.receiver_app import error_response, health_endpoint

logger = logging.getLogger(__name__)


def build_peer_file_server_app(storage: Storage, worker_id: str, role: str) -> Starlette:
    async def serve_raw(request: Request) -> Response:
        job_id = request.path_params["job_id"]
        raw_path = storage.find_raw(job_id)
        if raw_path is None:
            return error_response(404, ErrorCode.NOT_FOUND, f"raw {job_id} is not held here")
        logger.info("serving raw to %s", request.client.host if request.client else "?",
                    extra={"job_id": job_id})  # fmt: skip
        # FileResponse answers Range requests with 206 + Content-Range.
        return FileResponse(raw_path, media_type="application/octet-stream")

    return Starlette(
        routes=[
            Route("/health", health_endpoint(worker_id, role), methods=["GET"]),
            Route("/raw/{job_id}", serve_raw, methods=["GET", "HEAD"]),
        ]
    )


class BackgroundServer:
    """Runs an ASGI app with uvicorn in a daemon thread; stop() ends it.

    uvicorn installs no signal handlers outside the main thread, so the
    daemon's own SIGTERM/CTRL-C handling stays in charge.
    """

    def __init__(self, app, host: str, port: int, name: str) -> None:
        config = uvicorn.Config(app, host=host, port=port, log_config=None, access_log=False)
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._serve, name=name, daemon=True)
        self.description = f"{name} on {host}:{port}"

    def _serve(self) -> None:
        try:
            self._server.run()
        except Exception:
            logger.exception("%s stopped with an error", self.description)

    def start(self) -> None:
        logger.info("starting %s", self.description)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._server.should_exit = True
        self._thread.join(timeout)
