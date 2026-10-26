"""Contract: the worker loop (heartbeat, next, claim, transfers, ACKs)."""

from fastapi import APIRouter, Body, Depends, Header, Response

from shared.models import (
    ChunkAccepted,
    ClaimRequest,
    ClaimResponse,
    DeliveredRequest,
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
from tools.openapi_contract.auth import (
    binary_file_responses,
    error_responses,
    not_implemented,
    octet_stream_body,
    worker_bearer,
)

router = APIRouter(dependencies=[Depends(worker_bearer)])

_CHUNK_BODY = octet_stream_body(
    "One chunk of the file, starting at byte Upload-Offset. See docs/api.md, Chunked uploads."
)


@router.post(
    "/workers/{worker_id}/heartbeat",
    tags=["worker loop"],
    summary="Every 15 s: disk, measurements, and renew listed leases",
    response_model=HeartbeatResponse,
    responses=error_responses(400, 401, 403),
)
def heartbeat(worker_id: str, body: HeartbeatRequest) -> HeartbeatResponse:
    """`worker_id` must be the caller's own id (else 403)."""
    not_implemented()


@router.post(
    "/workers/{worker_id}/next",
    tags=["worker loop"],
    summary="Pull: what should I do next? (an offer; claim it to start)",
    response_model=NextTaskResponse,
    responses=error_responses(400, 401, 403),
)
def next_task(worker_id: str, body: NextTaskRequest) -> NextTaskResponse:
    """Returns at most one task among `accept_kinds`, or `task: null`.

    Never offers `download_raw` to a studio or to a worker that is not
    accepting new raws; never offers `make_proxy`/`process` to a worker
    that is not a confirmed holder; only offers `deliver` to studios.
    """
    not_implemented()


@router.get(
    "/workers",
    tags=["worker loop"],
    summary="Peer list with tailnet addresses (for peer raw fetches and studio push)",
    response_model=PeersResponse,
    responses=error_responses(401),
)
def list_peers() -> PeersResponse:
    not_implemented()


@router.post(
    "/jobs/{job_id}/claim",
    tags=["worker loop"],
    summary="Take a lease on one task of a job",
    response_model=ClaimResponse,
    responses=error_responses(400, 401, 404, 409),
)
def claim(job_id: str, body: ClaimRequest) -> ClaimResponse:
    """409 `already_claimed` if someone else won; 409 `not_claimable` if
    the job is not in a state for that task or the caller is not eligible.
    Claiming `process` moves the job ready → claimed."""
    not_implemented()


@router.post(
    "/jobs/{job_id}/lease/renew",
    tags=["worker loop"],
    summary="Extend one lease (heartbeats also renew)",
    response_model=LeaseRenewResponse,
    responses=error_responses(401, 404, 409),
)
def renew_lease(job_id: str, body: LeaseRenewRequest) -> LeaseRenewResponse:
    """409 `lease_lost` if the lease already expired or was revoked."""
    not_implemented()


@router.get(
    "/jobs/{job_id}/raw",
    tags=["worker loop"],
    summary="Download the raw from the VPS inbox (supports Range)",
    response_class=Response,
    responses={
        **binary_file_responses("application/octet-stream"),
        **error_responses(401, 404, 416),
    },
)
def download_raw(job_id: str, range_header: str | None = Header(None, alias="Range")) -> Response:
    """404 once two holders confirmed and the VPS deleted its copy; fetch
    from a peer then (download_raw task `sources`)."""
    not_implemented()


@router.post(
    "/jobs/{job_id}/holder-confirm",
    tags=["worker loop"],
    summary="I hold the raw and its SHA-256 matched",
    response_model=HolderConfirmResponse,
    responses=error_responses(400, 401, 404, 409, 422),
)
def holder_confirm(job_id: str, body: HolderConfirmRequest) -> HolderConfirmResponse:
    """Ends the download_raw lease. 422 `hash_mismatch` if the reported hash
    differs from the job's (the caller is dropped as holder and must delete
    its copy)."""
    not_implemented()


@router.post(
    "/jobs/{job_id}/proxy",
    tags=["worker loop"],
    summary="Chunked upload of the 480p proxy (MP4)",
    response_model=ChunkAccepted,
    openapi_extra=_CHUNK_BODY,
    responses=error_responses(400, 401, 404, 409, 413, 422),
)
def upload_proxy(
    job_id: str,
    upload_offset: int = Header(..., alias="Upload-Offset"),
    upload_length: int = Header(..., alias="Upload-Length"),
    upload_sha256: str = Header(..., alias="Upload-SHA256"),
    lease_id: str = Header(..., alias="Lease-Id"),
) -> ChunkAccepted:
    """On the completing chunk: proxy stored, `proxy_available` true, a
    `received` job moves to proxy_ready → awaiting_review, and the
    make_proxy lease ends. Upload the thumbnail *before* the proxy."""
    not_implemented()


@router.put(
    "/jobs/{job_id}/thumbnail",
    tags=["worker loop"],
    summary="Upload the review-queue thumbnail (one JPEG, single request)",
    status_code=204,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {"image/jpeg": {"schema": {"type": "string", "format": "binary"}}},
        }
    },
    responses=error_responses(400, 401, 404, 409, 413),
)
def upload_thumbnail(job_id: str, lease_id: str = Header(..., alias="Lease-Id")) -> None:
    not_implemented()


@router.post(
    "/jobs/{job_id}/clip",
    tags=["worker loop"],
    summary="Chunked upload of the finished clip to the VPS outbox (fallback route)",
    response_model=ChunkAccepted,
    openapi_extra=_CHUNK_BODY,
    responses=error_responses(400, 401, 404, 409, 413, 422),
)
def upload_clip(
    job_id: str,
    upload_offset: int = Header(..., alias="Upload-Offset"),
    upload_length: int = Header(..., alias="Upload-Length"),
    upload_sha256: str = Header(..., alias="Upload-SHA256"),
    lease_id: str = Header(..., alias="Lease-Id"),
) -> ChunkAccepted:
    """On the completing chunk: clip stored in the outbox, job claimed →
    uploaded (route `outbox`), process lease released, and a `deliver` task
    becomes available to the studio."""
    not_implemented()


@router.post(
    "/jobs/{job_id}/delivered",
    tags=["delivery"],
    summary="Studio ACK after a direct push from a worker",
    response_model=JobStateResponse,
    responses=error_responses(400, 401, 403, 404, 409),
)
def delivered(job_id: str, body: DeliveredRequest = Body(...)) -> JobStateResponse:
    """Studio role only (403 otherwise). Accepted from `claimed` (the
    orchestrator records claimed → uploaded → delivered, route `direct`)
    or from `uploaded`. Then → done. Repeating the call is a no-op 200."""
    not_implemented()
