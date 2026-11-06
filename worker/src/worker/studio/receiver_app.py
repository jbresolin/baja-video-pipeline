"""Studio receiver on port 8720: workers push finished clips here.

    GET  /health               {"status": "ok", "worker_id": …, "role": "studio"}
    POST /receive/{job_id}     chunked upload (docs/api.md §6.2) + Clip-Relative-Path

The bytes are assembled by shared.upload_receiver.ChunkedUploadReceiver,
the same class the orchestrator uses, so both receivers behave the same
way (offset corrections, hash check, "already complete"). The final clip
is moved into <box root>/<prova>/<car>/<name>.mp4 only once its SHA-256
matched, by StudioLibrary.place_clip (item 13): a clip already there with
the same content is "delivered" at once, a locked clip on air is never
overwritten (the new version goes beside it as …-v2.mp4). Then the
acknowledger updates latest.m3u and ACKs, with the name actually used.

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
from worker.studio.acknowledger import DeliveryAcknowledger
from worker.studio.library import StudioLibrary, UnsafeClipPathError
from worker.studio.pending_acks import PendingDelivery

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

    def accept_chunk_locked(
        relative_path: str, destination: Path, upload: UploadHeaders, chunk: bytes
    ) -> tuple[ChunkAccepted, str | None]:
        """The chunk's answer, and (once complete) the name the clip got."""
        with locks_guard:
            lock = locks[destination]
        with lock:
            if upload.offset == 0:
                # Same content already in the folder (a second delivery of a
                # clip the studio has): done, nothing to rename.
                already = library.find_same_content(relative_path, upload.sha256)
                if already is not None:
                    complete = ChunkAccepted(received_bytes=upload.total_length, complete=True)
                    return complete, already
            placed: dict[str, str] = {}

            def place(partial: Path, sha256: str) -> None:
                placed["path"] = library.place_clip(partial, relative_path, sha256)

            receiver = ChunkedUploadReceiver(
                destination,
                place=place,
                known_sha256=lambda _path: library.sha256_of(relative_path),
            )
            accepted = receiver.accept_chunk(upload, chunk)
            if not accepted.complete:
                return accepted, None
            # "Already complete" answers do not call place(): find which name has it.
            final = placed.get("path") or library.find_same_content(relative_path, upload.sha256)
            return accepted, final or relative_path

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
                accepted, stored_as = await run_in_threadpool(
                    accept_chunk_locked, relative_path, destination, upload, chunk
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
                logger.info("clip received at %s", stored_as)
                acknowledger.submit(
                    PendingDelivery(job_id, stored_as, upload.sha256, upload.total_length)
                )
            return JSONResponse(accepted.model_dump())

    return Starlette(
        routes=[
            Route("/health", health_endpoint(worker_id, "studio"), methods=["GET"]),
            Route("/receive/{job_id}", receive, methods=["POST"]),
        ]
    )
