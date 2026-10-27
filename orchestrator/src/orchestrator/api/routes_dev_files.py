"""Routes (dev mode only): /inbox/* /outbox/* /proxies/* /thumbs/* without Caddy.

create_app() includes this router only when BVP_DEV_MODE=1, and the
routes are left out of the OpenAPI document: they are Caddy's paths, not
part of the orchestrator's contract (design §15, §18).
FileResponse answers Range requests with 206, like Caddy's file_server,
so worker downloads resume and the review player can seek.
"""

import sqlite3

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse

from orchestrator.api.auth import get_context, require_reviewer, require_worker
from orchestrator.context import OrchestratorContext
from orchestrator.domain import dev_files

router = APIRouter(include_in_schema=False)

# HEAD too: downloaders may ask for the size first, as they can with Caddy.
READ_METHODS = ["GET", "HEAD"]


@router.api_route("/inbox/{file_name}", methods=READ_METHODS)
def inbox_file(
    file_name: str,
    request: Request,
    worker_row: sqlite3.Row = Depends(require_worker),
    context: OrchestratorContext = Depends(get_context),
) -> FileResponse:
    path = dev_files.worker_file_path(context, worker_row, "inbox", file_name, request.method)
    return FileResponse(path, headers={"X-Worker-Id": worker_row["id"]})


@router.api_route("/outbox/{file_name}", methods=READ_METHODS)
def outbox_file(
    file_name: str,
    request: Request,
    worker_row: sqlite3.Row = Depends(require_worker),
    context: OrchestratorContext = Depends(get_context),
) -> FileResponse:
    path = dev_files.worker_file_path(context, worker_row, "outbox", file_name, request.method)
    return FileResponse(path, headers={"X-Worker-Id": worker_row["id"]})


@router.api_route("/proxies/{file_name}", methods=READ_METHODS)
def proxy_file(
    file_name: str,
    reviewer: str = Depends(require_reviewer),
    context: OrchestratorContext = Depends(get_context),
) -> FileResponse:
    path = dev_files.review_media_path(context, "proxies", file_name)
    return FileResponse(path, media_type="video/mp4")


@router.api_route("/thumbs/{file_name}", methods=READ_METHODS)
def thumbnail_file(
    file_name: str,
    reviewer: str = Depends(require_reviewer),
    context: OrchestratorContext = Depends(get_context),
) -> FileResponse:
    path = dev_files.review_media_path(context, "thumbs", file_name)
    return FileResponse(path, media_type="image/jpeg")
