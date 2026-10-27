"""Routes: review UI endpoints, manifest, outbox ACK and the fleet view."""

import sqlite3
from datetime import datetime

from fastapi import APIRouter, Depends, Query

from orchestrator.api.auth import (
    get_context,
    require_reviewer,
    require_reviewer_or_admin,
    require_reviewer_or_worker,
    require_worker,
)
from orchestrator.context import OrchestratorContext
from orchestrator.domain import delivery, fleet, review, review_queue
from shared.models import (
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

router = APIRouter()


@router.get(
    "/review/queue",
    tags=["review"],
    response_model=ReviewQueueResponse,
    dependencies=[Depends(require_reviewer)],
)
def list_review_queue(
    state: list[JobState] = Query([JobState.AWAITING_REVIEW]),
    limit: int = Query(100, ge=1, le=500),
    context: OrchestratorContext = Depends(get_context),
) -> ReviewQueueResponse:
    return review_queue.review_queue(context, state, limit)


@router.get(
    "/jobs/{job_id}",
    tags=["review"],
    response_model=Job,
    dependencies=[Depends(require_reviewer_or_worker)],
)
def get_job(job_id: str, context: OrchestratorContext = Depends(get_context)) -> Job:
    return review_queue.get_job(context, job_id)


@router.patch("/jobs/{job_id}/review", tags=["review"], response_model=Job)
def update_review(
    job_id: str,
    body: ReviewUpdate,
    reviewer: str = Depends(require_reviewer),
    context: OrchestratorContext = Depends(get_context),
) -> Job:
    return review.update_review(context, reviewer, job_id, body)


@router.post(
    "/jobs/{job_id}/regenerate-proxy",
    tags=["review"],
    response_model=RegenerateProxyResponse,
    status_code=202,
)
def regenerate_proxy(
    job_id: str,
    reviewer: str = Depends(require_reviewer),
    context: OrchestratorContext = Depends(get_context),
) -> RegenerateProxyResponse:
    return review_queue.regenerate_proxy(context, reviewer, job_id)


@router.get(
    "/teams/{car_number}",
    tags=["review"],
    response_model=Team,
    dependencies=[Depends(require_reviewer)],
)
def get_team(car_number: int, context: OrchestratorContext = Depends(get_context)) -> Team:
    return review_queue.find_team(context, car_number)


@router.get(
    "/manifest",
    tags=["delivery"],
    response_model=ManifestResponse,
    dependencies=[Depends(require_reviewer_or_worker)],
)
def manifest(
    since: datetime | None = Query(None),
    prova: str | None = Query(None),
    context: OrchestratorContext = Depends(get_context),
) -> ManifestResponse:
    return delivery.build_manifest(context, since, prova)


@router.post("/outbox/{job_id}/ack", tags=["delivery"], response_model=JobStateResponse)
def outbox_ack(
    job_id: str,
    body: OutboxAckRequest,
    worker_row: sqlite3.Row = Depends(require_worker),
    context: OrchestratorContext = Depends(get_context),
) -> JobStateResponse:
    return delivery.acknowledge_outbox_pull(context, worker_row, job_id, body)


@router.get(
    "/fleet",
    tags=["fleet"],
    response_model=FleetResponse,
    dependencies=[Depends(require_reviewer_or_admin)],
)
def get_fleet(context: OrchestratorContext = Depends(get_context)) -> FleetResponse:
    return fleet.build_fleet(context)
