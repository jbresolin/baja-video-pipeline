"""Contract: review UI endpoints, delivery (manifest, outbox ACK) and fleet view.

Outbox *bytes* are served by Caddy (/outbox/* on the tailnet listener,
guarded by GET /auth/file), not by the orchestrator (design §15).
"""

from datetime import datetime

from fastapi import APIRouter, Depends, Query

from shared.models import (
    EventsResponse,
    FleetResponse,
    Job,
    JobState,
    JobStateResponse,
    ManifestResponse,
    OutboxAckRequest,
    RegenerateProxyResponse,
    ReviewQueueResponse,
    ReviewUpdate,
    Team,
)
from tools.openapi_contract.auth import (
    admin_bearer,
    error_responses,
    not_implemented,
    reviewer_basic,
    worker_bearer,
)

router = APIRouter()

# Two dependencies on one route mean "either credential is accepted".
_reviewer_only = [Depends(reviewer_basic)]
_reviewer_or_worker = [Depends(reviewer_basic), Depends(worker_bearer)]
_reviewer_or_admin = [Depends(reviewer_basic), Depends(admin_bearer)]


# --- Review ---


@router.get(
    "/review/queue",
    tags=["review"],
    summary="Jobs waiting for review, oldest first",
    response_model=ReviewQueueResponse,
    dependencies=_reviewer_only,
    responses=error_responses(401),
)
def review_queue(
    state: list[JobState] = Query(
        [JobState.AWAITING_REVIEW],
        description="Repeat to include several states, e.g. ?state=awaiting_review&state=ready",
    ),
    limit: int = Query(100, ge=1, le=500),
) -> ReviewQueueResponse:
    not_implemented()


@router.get(
    "/jobs/{job_id}",
    tags=["review"],
    summary="Full job record",
    response_model=Job,
    dependencies=_reviewer_or_worker,
    responses=error_responses(401, 404),
)
def get_job(job_id: str) -> Job:
    not_implemented()


@router.patch(
    "/jobs/{job_id}/review",
    tags=["review"],
    summary="Save fields and cuts; optionally mark ready or reprocess",
    response_model=Job,
    dependencies=_reviewer_only,
    responses=error_responses(400, 401, 404, 409, 422),
)
def update_review(job_id: str, body: ReviewUpdate) -> Job:
    """* `expected_version` ≠ current → 409 `version_conflict` (reload and retry).
    * `action: ready` from awaiting_review → ready.
    * `action: reprocess` from claimed/uploaded/delivered/done → ready; any
      process lease is revoked (the worker sees `lost` on its next heartbeat).
    * `action: discard` from awaiting_review/ready/failed → discarded (final):
      leases revoked, the VPS inbox copy deleted, and holders get the job in
      `deletable_job_ids` on their next heartbeat. Fields are not required.
    * `action: retry` from failed → `Job.failed_from` (`received`,
      `proxy_ready`, or `ready` for a job that failed while `claimed`);
      clears `Job.failure` and `failed_from`. Fields and cuts may be
      corrected in the same request; they are required only when the
      target is `ready`.
    * Other state/action combinations → 409 `illegal_transition`.
    * ready/reprocess with prova, car_number, passagem or a cut missing → 422
      `missing_review_fields` (details.missing lists them).
    * `team_name` is resolved from car_number when it changes.
    * cut_out_s beyond the known duration → 422 `validation_error`.
    """
    not_implemented()


@router.post(
    "/jobs/{job_id}/regenerate-proxy",
    tags=["review"],
    summary="Ask a holder to re-render an evicted proxy",
    response_model=RegenerateProxyResponse,
    status_code=202,
    dependencies=_reviewer_only,
    responses=error_responses(401, 404),
)
def regenerate_proxy(job_id: str) -> RegenerateProxyResponse:
    """Does not change the job state. If no holder is online, `queued` is
    still true and `waiting_reason` says so; the task waits."""
    not_implemented()


@router.get(
    "/teams/{car_number}",
    tags=["review"],
    summary="Team for a car number, from the Baja app",
    response_model=Team,
    dependencies=_reviewer_only,
    responses=error_responses(401, 404, 503),
)
def get_team(car_number: int) -> Team:
    not_implemented()


# --- Delivery ---


@router.get(
    "/manifest",
    tags=["delivery"],
    summary="Delivered clips: prova, car, passagem, path, SHA-256",
    response_model=ManifestResponse,
    dependencies=_reviewer_or_worker,
    responses=error_responses(401),
)
def manifest(
    since: datetime | None = Query(None, description="Only clips delivered after this time"),
    prova: str | None = Query(None, description="Only this prova (slug or name)"),
) -> ManifestResponse:
    """Newest first. Only the latest delivery per (prova, car, passagem)."""
    not_implemented()


@router.post(
    "/outbox/{job_id}/ack",
    tags=["delivery"],
    summary="Studio confirms it stored a verified copy; VPS deletes the outbox file",
    response_model=JobStateResponse,
    dependencies=[Depends(worker_bearer)],
    responses=error_responses(400, 401, 403, 404, 409, 422),
)
def outbox_ack(job_id: str, body: OutboxAckRequest) -> JobStateResponse:
    """Studio role only. uploaded → delivered → done; ends the deliver lease.
    422 `hash_mismatch` if `sha256` differs from the clip's."""
    not_implemented()


# --- Event log ---


@router.get(
    "/jobs/{job_id}/events",
    tags=["events"],
    summary="One job's event log, oldest first",
    response_model=EventsResponse,
    dependencies=_reviewer_or_admin,
    responses=error_responses(401, 404),
)
def job_events(job_id: str) -> EventsResponse:
    """Every event recorded for the job (state changes, leases, holders,
    failures), oldest first. 404 if the job does not exist."""
    not_implemented()


@router.get(
    "/events",
    tags=["events"],
    summary="The event log across jobs and workers, oldest first",
    response_model=EventsResponse,
    dependencies=_reviewer_or_admin,
    responses=error_responses(400, 401),
)
def events(
    worker_id: str | None = Query(None, description="Only events about this worker"),
    since: datetime | None = Query(None, description="Only events strictly after this time"),
    limit: int = Query(100, ge=1, le=1000),
) -> EventsResponse:
    """Oldest first. With `since`, the first `limit` events after it (page
    forward by passing the last `at` back). Without `since`, the newest
    `limit` events, still returned oldest first."""
    not_implemented()


# --- Fleet ---


@router.get(
    "/fleet",
    tags=["fleet"],
    summary="Workers with stats, disk and leases; VPS disk; job counts",
    response_model=FleetResponse,
    dependencies=_reviewer_or_admin,
    responses=error_responses(401),
)
def fleet() -> FleetResponse:
    not_implemented()
