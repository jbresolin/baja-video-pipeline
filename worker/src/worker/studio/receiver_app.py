"""Studio receiver on port 8720: workers push finished clips here.

    GET  /health               {"status": "ok", "worker_id": …, "role": "studio"}
    POST /receive/{job_id}     chunked upload (docs/api.md §6.2) + Clip-Relative-Path

The bytes are assembled by shared.upload_receiver.ChunkedUploadReceiver,
the same class the orchestrator uses, so both receivers behave the same
way (offset corrections, hash check, "already complete"). The final clip
is renamed into <box root>/<prova>/<car>/<passagem>.mp4 only once its
SHA-256 matched. Then the acknowledger updates latest.m3u and ACKs.

No app-level auth in v1: the tailnet ACL is the boundary (design §15).
"""

import logging
import threading
from collections import defaultdict
from pathlib import Path

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from shared.models import ChunkAccepted, ErrorCode
from shared.protocol import CLIP_RELATIVE_PATH_HEADER
from shared.upload_receiver import (
    ChunkedUploadReceiver,
    InvalidUploadHeadersError,
    UploadHashMismatchError,
    UploadHeaders,
    UploadOffsetMismatchError,
    UploadTooLargeError,
    read_upload_headers,
)
from worker.logging_setup import job_context
from worker.studio.acknowledger import DeliveryAcknowledger, PendingDelivery
from worker.studio.library import StudioLibrary, UnsafeClipPathError

logger = logging.getLogger(__name__)


def error_response(status: int, code: ErrorCode, message: str, **details) -> JSONResponse:
    """The API's one error shape (docs/api.md §3)."""
    body = {"error": {"code": code.value, "message": message, "details": details}}
    return JSONResponse(body, status_code=status)


def health_endpoint(worker_id: str, role: str):
    async def health(request: Request) -> Response:
        return JSONResponse({"status": "ok", "worker_id": worker_id, "role": role})

    return health


def build_receiver_app(
    library: StudioLibrary, acknowledger: DeliveryAcknowledger, worker_id: str
) -> Starlette:
    # ChunkedUploadReceiver is not thread-safe: one lock per destination
    # file, so two chunks for the same clip are never written at once.
    locks: defaultdict[Path, threading.Lock] = defaultdict(threading.Lock)
    locks_guard = threading.Lock()

    def accept_chunk_locked(destination: Path, upload: UploadHeaders, chunk: bytes):
        with locks_guard:
            lock = locks[destination]
        with lock:
            return ChunkedUploadReceiver(destination).accept_chunk(upload, chunk)

    async def receive(request: Request) -> Response:
        job_id = request.path_params["job_id"]
        with job_context(job_id):
            relative_path = request.headers.get(CLIP_RELATIVE_PATH_HEADER, "")
            try:
                destination = library.absolute_path(relative_path)
                upload = read_upload_headers(request.headers)
            except (UnsafeClipPathError, InvalidUploadHeadersError) as error:
                logger.warning("refused push: %s", error)
                return error_response(400, ErrorCode.VALIDATION_ERROR, str(error))

            chunk = await request.body()
            try:
                # Hashing a finished clip takes a moment: keep it off the event loop.
                accepted: ChunkAccepted = await run_in_threadpool(
                    accept_chunk_locked, destination, upload, chunk
                )
            except UploadOffsetMismatchError as error:
                return error_response(
                    409,
                    ErrorCode.UPLOAD_OFFSET_MISMATCH,
                    str(error),
                    expected_offset=error.expected_offset,
                )
            except UploadHashMismatchError as error:
                logger.warning("pushed clip failed its hash check: %s", error)
                return error_response(422, ErrorCode.HASH_MISMATCH, str(error))
            except UploadTooLargeError as error:
                return error_response(413, ErrorCode.UPLOAD_TOO_LARGE, str(error))

            if accepted.complete:
                logger.info("clip received at %s", relative_path)
                acknowledger.submit(
                    PendingDelivery(job_id, relative_path, upload.sha256, upload.total_length)
                )
            return JSONResponse(accepted.model_dump())

    return Starlette(
        routes=[
            Route("/health", health_endpoint(worker_id, "studio"), methods=["GET"]),
            Route("/receive/{job_id}", receive, methods=["POST"]),
        ]
    )
