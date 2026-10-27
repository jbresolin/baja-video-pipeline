"""Test doubles for the worker: an in-memory "network" and a fake ffmpeg.

RoutingTransport: one httpx transport that sends each request to an
in-process app chosen by host and port, e.g. the fake orchestrator at
orchestrator.test:8080, a peer's file server at peer.test:8710, the studio
receiver at studio.test:8720. A host with no app behaves like a machine
that is offline (httpx.ConnectError).

FakeMediaTools: the real MediaTools with the two places that would run a
program replaced. probe() returns a MediaInfo the test chose; run_ffmpeg()
records the command line (built by the real shared.ffmpeg builders) and
writes a small file where ffmpeg would have written its output.
"""

from collections.abc import Callable
from pathlib import Path

import httpx

from shared.media_probe import MediaInfo
from shared.testing.asgi_transport import SyncAsgiTransport
from worker.leases import CancelToken
from worker.media_tools import MediaToolError, MediaTools

FULL_HD_WITH_AUDIO = MediaInfo(
    duration_s=12.0, has_audio=True, width=1920, height=1080, color_transfer="bt709"
)


class RoutingTransport(httpx.BaseTransport):
    def __init__(self) -> None:
        self._routes: dict[str, httpx.BaseTransport] = {}
        # (host:port, path prefix) → transport, checked before _routes.
        self._path_overrides: dict[tuple[str, str], httpx.BaseTransport] = {}
        self.requested_urls: list[str] = []

    def add_app(self, base_url: str, app) -> None:
        self.add_transport(base_url, SyncAsgiTransport(app))

    def add_transport(self, base_url: str, transport: httpx.BaseTransport) -> None:
        url = httpx.URL(base_url)
        self._routes[f"{url.host}:{url.port}"] = transport

    def override_path(self, base_url: str, path_prefix: str, transport: httpx.BaseTransport):
        """Send requests under `path_prefix` on that host to `transport` instead."""
        url = httpx.URL(base_url)
        self._path_overrides[(f"{url.host}:{url.port}", path_prefix)] = transport

    def remove(self, base_url: str) -> None:
        url = httpx.URL(base_url)
        self._routes.pop(f"{url.host}:{url.port}", None)

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.requested_urls.append(str(request.url))
        host_port = f"{request.url.host}:{request.url.port}"
        for (override_host, prefix), override in self._path_overrides.items():
            if override_host == host_port and request.url.path.startswith(prefix):
                return override.handle_request(request)
        transport = self._routes.get(host_port)
        if transport is None:
            raise httpx.ConnectError(f"{request.url.host} is offline", request=request)
        return transport.handle_request(request)


class FakeMediaTools(MediaTools):
    def __init__(self, info: MediaInfo = FULL_HD_WITH_AUDIO) -> None:
        super().__init__("ffmpeg", "ffprobe", "libx264")
        self.info = info
        # (what, args) of every ffmpeg command, in order.
        self.commands: list[tuple[str, list[str]]] = []
        self.probed_paths: list[Path] = []
        # Set to make the next probe / the named render fail.
        self.probe_error: MediaToolError | None = None
        self.failing_render: str | None = None
        # Called just before a render "runs": lets a test revoke a lease or
        # request shutdown while the render is in progress.
        self.during_render: Callable[[str], None] | None = None

    def probe(self, media_path: Path) -> MediaInfo:
        self.probed_paths.append(media_path)
        if self.probe_error is not None:
            raise self.probe_error
        return self.info

    def run_ffmpeg(self, args: list[str], cancel: CancelToken, *, what: str) -> None:
        self.commands.append((what, args))
        if self.during_render is not None:
            self.during_render(what)
        # A real render would notice the cancellation while polling ffmpeg.
        cancel.raise_if_cancelled()
        if self.failing_render == what:
            raise MediaToolError(f"ffmpeg {what} failed with exit code 1:\nmoov atom not found", 1)
        Path(args[-1]).write_bytes(f"fake {what} output ".encode() * 1000)

    def command_for(self, what: str) -> list[str]:
        return next(args for name, args in self.commands if name == what)
