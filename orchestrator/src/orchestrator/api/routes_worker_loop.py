"""Routes: the worker loop (heartbeat, next, claim, results, uploads, file auth)."""

import sqlite3

from fastapi import APIRouter, Depends, Header, Request, Response
from starlette.concurrency import run_in_threadpool

from orchestrator.api.auth import (
    get_context,
    require_worker,
    require_worker_matching_path,
)
from orchestrator.context import OrchestratorContext
from orchestrator.domain import delivery, fleet, task_results, uploads, worker_loop
from orchestrator.domain.file_access import authorize_file_request
from shared.models import (
    ChunkAccepted,
    ClaimRequest,
    ClaimResponse,
    DeliveredRequest,
    FailedRequest,
    HeartbeatRequest,
    HeartbeatResponse,
    HolderConfirmRequest,
    HolderConfirmResponse,
    JobStateResponse,
    LeaseRenewRequest,
    LeaseRenewResponse,
    NextTaskRequest,
    NextTaskResponse,
    PeersResponse,
)
from shared.protocol import LEASE_ID_HEADER

router = APIRouter(tags=["worker loop"])


@router.post("/workers/{worker_id}/heartbeat", response_model=HeartbeatResponse)
def heartbeat(
    body: HeartbeatRequest,
    worker_row: sqlite3.Row = Depends(require_worker_matching_path),
    context: OrchestratorContext = Depends(get_context),
) -> HeartbeatResponse:
    return worker_loop.process_heartbeat(context, worker_row, body)


@router.post("/workers/{worker_id}/next", response_model=NextTaskResponse)
def next_task(
    body: NextTaskRequest,
    worker_row: sqlite3.Row = Depends(require_worker_matching_path),
    context: OrchestratorContext = Depends(get_context),
) -> NextTaskResponse:
    return worker_loop.offer_next_task(context, worker_row, body)


@router.get("/workers", response_model=PeersResponse, dependencies=[Depends(require_worker)])
def list_peers(context: OrchestratorContext = Depends(get_context)) -> PeersResponse:
    return fleet.list_peers(context)


@router.post("/jobs/{job_id}/claim", response_model=ClaimResponse)
def claim(
    job_id: str,
    body: ClaimRequest,
    worker_row: sqlite3.Row = Depends(require_worker),
    context: OrchestratorContext = Depends(get_context),
) -> ClaimResponse:
    return worker_loop.claim_task(context, worker_row, job_id, body.task_kind)


@router.post("/jobs/{job_id}/lease/renew", response_model=LeaseRenewResponse)
def renew_lease(
    job_id: str,
    body: LeaseRenewRequest,
    worker_row: sqlite3.Row = Depends(require_worker),
    context: OrchestratorContext = Depends(get_context),
) -> LeaseRenewResponse:
    return worker_loop.renew_one_lease(context, worker_row, job_id, body.lease_id)


@router.get("/auth/file", status_code=204, response_class=Response)
def authorize_file(
    forwarded_uri: str = Header(..., alias="X-Forwarded-Uri"),
    forwarded_method: str | None = Header(None, alias="X-Forwarded-Method"),
    worker_row: sqlite3.Row = Depends(require_worker),
    context: OrchestratorContext = Depends(get_context),
) -> Response:
    authorize_file_request(context, worker_row, forwarded_uri, forwarded_method)
    return Response(status_code=204, headers={"X-Worker-Id": worker_row["id"]})


@router.post("/jobs/{job_id}/failed", response_model=JobStateResponse)
def report_failure(
    job_id: str,
    body: FailedRequest,
    worker_row: sqlite3.Row = Depends(require_worker),
    context: OrchestratorContext = Depends(get_context),
) -> JobStateResponse:
    return task_results.report_failure(context, worker_row, job_id, body)


@router.post("/jobs/{job_id}/holder-confirm", response_model=HolderConfirmResponse)
def holder_confirm(
    job_id: str,
    body: HolderConfirmRequest,
    worker_row: sqlite3.Row = Depends(require_worker),
    context: OrchestratorContext = Depends(get_context),
) -> HolderConfirmResponse:
    return task_results.confirm_holder(context, worker_row, job_id, body)


# Uploads are async only to read the body; the file and DB work runs in a thread.


@router.post("/jobs/{job_id}/proxy", response_model=ChunkAccepted)
async def upload_proxy(
    job_id: str,
    request: Request,
    lease_id: str = Header(..., alias=LEASE_ID_HEADER),
    worker_row: sqlite3.Row = Depends(require_worker),
    context: OrchestratorContext = Depends(get_context),
) -> ChunkAccepted:
    chunk = await request.body()
    return await run_in_threadpool(
        uploads.accept_proxy_chunk, context, worker_row, job_id, lease_id, request.headers, chunk
    )


@router.put("/jobs/{job_id}/thumbnail", status_code=204, response_class=Response)
async def upload_thumbnail(
    job_id: str,
    request: Request,
    lease_id: str = Header(..., alias=LEASE_ID_HEADER),
    worker_row: sqlite3.Row = Depends(require_worker),
    context: OrchestratorContext = Depends(get_context),
) -> Response:
    jpeg = await request.body()
    await run_in_threadpool(uploads.store_thumbnail, context, worker_row, job_id, lease_id, jpeg)
    return Response(status_code=204)


@router.post("/jobs/{job_id}/clip", response_model=ChunkAccepted)
async def upload_clip(
    job_id: str,
    request: Request,
    lease_id: str = Header(..., alias=LEASE_ID_HEADER),
    worker_row: sqlite3.Row = Depends(require_worker),
    context: OrchestratorContext = Depends(get_context),
) -> ChunkAccepted:
    chunk = await request.body()
    return await run_in_threadpool(
        uploads.accept_clip_chunk, context, worker_row, job_id, lease_id, request.headers, chunk
    )


@router.post("/jobs/{job_id}/delivered", response_model=JobStateResponse, tags=["delivery"])
def delivered(
    job_id: str,
    body: DeliveredRequest,
    worker_row: sqlite3.Row = Depends(require_worker),
    context: OrchestratorContext = Depends(get_context),
) -> JobStateResponse:
    return delivery.acknowledge_direct_push(context, worker_row, job_id, body)
