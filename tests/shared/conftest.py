"""Shared test helpers for the `shared` package.

pytest loads this file automatically before the tests in this folder, and
every function decorated with @pytest.fixture becomes something a test can
ask for just by naming it as a parameter.

The main helper here is a tiny web server that lives inside the test
process. Tests talk to it through a normal httpx.Client whose "transport"
(SyncAsgiTransport) calls the web app directly in memory instead of
opening a network connection. So no ports, no real network, and nothing
that can be flaky because of the machine the tests run on.
"""

from dataclasses import dataclass, field
from pathlib import Path

import httpx
import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from shared.testing.asgi_transport import SyncAsgiTransport
from shared.upload_receiver import (
    ChunkedUploadReceiver,
    InvalidUploadHeadersError,
    UploadHashMismatchError,
    UploadOffsetMismatchError,
    UploadTooLargeError,
    read_upload_headers,
)


@dataclass
class FakeServer:
    """A running in-process server plus a log of what it received."""

    client: httpx.Client
    # The headers of every GET /file request, oldest first, so tests can
    # check e.g. that a resumed download really sent a Range header.
    download_request_headers: list[dict[str, str]] = field(default_factory=list)
    # The (status code, Upload-Offset) of every POST /upload, oldest first.
    upload_log: list[tuple[int, int]] = field(default_factory=list)


def _error(status: int, code: str, message: str, **details) -> JSONResponse:
    """Answer in the API's standard error shape: {"error": {...}}."""
    body = {"error": {"code": code, "message": message, "details": details}}
    return JSONResponse(body, status_code=status)


def build_fake_server(
    file_content: bytes,
    upload_destination: Path,
    *,
    honour_range: bool = True,
) -> FakeServer:
    """Create the in-process server.

    GET  /file    serves `file_content`, with Range support unless
                  `honour_range` is False (then it always sends everything,
                  like some simple servers do).
    POST /upload  receives chunked uploads into `upload_destination` using
                  the real ChunkedUploadReceiver from the shared package.
    """
    download_request_headers: list[dict[str, str]] = []
    upload_log: list[tuple[int, int]] = []
    receiver = ChunkedUploadReceiver(upload_destination)

    async def serve_file(request: Request) -> Response:
        download_request_headers.append(dict(request.headers))
        range_header = request.headers.get("range")
        if not honour_range or range_header is None:
            return Response(file_content, status_code=200)
        # We only need the "bytes=<start>-" form, which is all our client sends.
        start = int(range_header.removeprefix("bytes=").removesuffix("-"))
        total = len(file_content)
        if start >= total:
            return Response(status_code=416, headers={"Content-Range": f"bytes */{total}"})
        return Response(
            file_content[start:],
            status_code=206,
            headers={"Content-Range": f"bytes {start}-{total - 1}/{total}"},
        )

    async def receive_upload(request: Request) -> Response:
        chunk = await request.body()
        try:
            upload = read_upload_headers(request.headers)
            accepted = receiver.accept_chunk(upload, chunk)
        except InvalidUploadHeadersError as error:
            response = _error(400, "validation_error", str(error))
        except UploadOffsetMismatchError as error:
            response = _error(
                409, "upload_offset_mismatch", str(error), expected_offset=error.expected_offset
            )
        except UploadHashMismatchError as error:
            response = _error(422, "hash_mismatch", str(error))
        except UploadTooLargeError as error:
            response = _error(413, "upload_too_large", str(error))
        else:
            response = JSONResponse(accepted.model_dump())
        upload_log.append((response.status_code, int(request.headers["upload-offset"])))
        return response

    app = Starlette(
        routes=[
            Route("/file", serve_file, methods=["GET"]),
            Route("/upload", receive_upload, methods=["POST"]),
        ]
    )
    client = httpx.Client(transport=SyncAsgiTransport(app), base_url="http://testserver")
    return FakeServer(client, download_request_headers, upload_log)


@pytest.fixture
def fake_server_factory(tmp_path):
    """Lets a test build a FakeServer with the content it wants.

    `tmp_path` is a fresh empty folder pytest creates for each test, so
    files from one test never leak into another.
    """

    def factory(file_content: bytes, *, honour_range: bool = True) -> FakeServer:
        return build_fake_server(
            file_content, tmp_path / "server" / "uploaded.bin", honour_range=honour_range
        )

    return factory


@pytest.fixture
def sample_bytes() -> bytes:
    """About 300 KB of non-repeating bytes, so a wrong offset changes the hash."""
    return bytes((index * 7 + index // 251) % 256 for index in range(300_000))
