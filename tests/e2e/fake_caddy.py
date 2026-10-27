"""A stand-in for Caddy's tailnet listener (:8080), as an in-memory httpx transport.

On the VPS, Caddy serves /inbox/* and /outbox/* itself after asking the
orchestrator's GET /auth/file (forward_auth, design §15), and passes every
other request to the orchestrator. This transport does the same three
things, so the worker code under test sees the same answers it would see
in production:

  1. /inbox/<file> or /outbox/<file>: send GET /auth/file to the
     orchestrator with the worker's Authorization header and the path in
     X-Forwarded-Uri. Anything but 2xx is returned to the worker as is.
  2. Allowed: serve the file from the orchestrator's data folder, honouring
     "Range: bytes=<start>-" (206), like Caddy's file_server. Dot-files are
     hidden, as in the Caddyfile.
  3. Any other path: forwarded to the orchestrator unchanged.
"""

import re
from pathlib import Path

import httpx

FILE_PREFIXES = ("/inbox/", "/outbox/")
RANGE_PATTERN = re.compile(r"bytes=(\d+)-$")


class FakeCaddyTransport(httpx.BaseTransport):
    def __init__(self, orchestrator: httpx.BaseTransport, data_dir: Path) -> None:
        self._orchestrator = orchestrator
        self._data_dir = data_dir
        # Every file path Caddy served (allowed by /auth/file), for assertions.
        self.served_paths: list[str] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if not path.startswith(FILE_PREFIXES):
            return self._orchestrator.handle_request(request)
        verdict = self._ask_orchestrator(request)
        if not 200 <= verdict.status_code < 300:
            return verdict
        return self._serve_file(request, path)

    def _ask_orchestrator(self, request: httpx.Request) -> httpx.Response:
        headers = {"X-Forwarded-Uri": request.url.path, "X-Forwarded-Method": request.method}
        if "authorization" in request.headers:
            headers["Authorization"] = request.headers["authorization"]
        auth_request = httpx.Request("GET", request.url.copy_with(path="/auth/file", query=None))
        auth_request.headers.update(headers)
        response = self._orchestrator.handle_request(auth_request)
        response.read()
        return response

    def _serve_file(self, request: httpx.Request, path: str) -> httpx.Response:
        file_name = path.rsplit("/", 1)[-1]
        file_path = self._data_dir / path.lstrip("/")
        if file_name.startswith(".") or not file_path.is_file():
            return httpx.Response(404)
        data = file_path.read_bytes()
        self.served_paths.append(path)
        match = RANGE_PATTERN.fullmatch(request.headers.get("range", ""))
        if match is None:
            return httpx.Response(200, content=data)
        start = int(match.group(1))
        if start >= len(data):
            return httpx.Response(416, headers={"Content-Range": f"bytes */{len(data)}"})
        content_range = f"bytes {start}-{len(data) - 1}/{len(data)}"
        return httpx.Response(206, content=data[start:], headers={"Content-Range": content_range})
