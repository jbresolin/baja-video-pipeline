"""Routes: review UI endpoints, manifest, outbox ACK and the fleet view."""

import sqlite3
from datetime import datetime

from fastapi import APIRouter, Depends, Query, Response

from orchestrator.api.auth import (
    get_context,
    require_reviewer,
    require_reviewer_or_admin,
    require_reviewer_or_worker,
    require_worker,
)
from orchestrator.context import OrchestratorContext
from orchestrator.domain import (
    car_videos,
    delivery,
    event_log,
    fleet,
    originals,
    pass_preview,
    review,
    review_queue,
    sender_messages,
)
from shared.models import (
    CarVideosResponse,
    EventsResponse,
    FleetResponse,
    Job,
    JobPresenceResponse,
    JobState,
    JobStateResponse,
    ManifestResponse,
    OriginalDownload,
    OutboxAckRequest,
    PassPreview,
    RegenerateProxyResponse,
    ReviewQueueResponse,
    ReviewUpdate,
    SenderMessagesResponse,
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
    outdated: bool = Query(False),
    context: OrchestratorContext = Depends(get_context),
) -> ReviewQueueResponse:
    return review_queue.review_queue(context, state, limit, outdated_only=outdated)


@router.get(
    "/review/car-videos",
    tags=["review"],
    response_model=CarVideosResponse,
    dependencies=[Depends(require_reviewer)],
)
def list_car_videos(
    prova: str = Query(..., min_length=1, max_length=200),
    car_number: int = Query(..., ge=0, le=9999),
    context: OrchestratorContext = Depends(get_context),
) -> CarVideosResponse:
    """One car's videos in one prova, discarded ones left out (LIVESTACK-45)."""
    return car_videos.car_videos(context, prova, car_number)


@router.get(
    "/jobs/{job_id}",
    tags=["review"],
    response_model=Job,
    dependencies=[Depends(require_reviewer_or_worker)],
)
def get_job(job_id: str, context: OrchestratorContext = Depends(get_context)) -> Job:
    return review_queue.get_job(context, job_id)


@router.get(
    "/jobs/{job_id}/sender-messages",
    tags=["review"],
    response_model=SenderMessagesResponse,
    dependencies=[Depends(require_reviewer)],
)
def get_sender_messages(
    job_id: str, context: OrchestratorContext = Depends(get_context)
) -> SenderMessagesResponse:
    """The job page's "Mensagens de <remetente>" (LIVESTACK-49). Reviewers
    only: workers read GET /jobs/{id} and never need people's messages."""
    return sender_messages.sender_messages_for_job(context, job_id)


@router.get(
    "/jobs/{job_id}/pass-preview",
    tags=["review"],
    response_model=PassPreview,
    dependencies=[Depends(require_reviewer)],
)
def get_pass_preview(
    job_id: str,
    prova: str | None = Query(None, min_length=1, max_length=64),
    car_number: int | None = Query(None, ge=0, le=9999),
    passagem: int | None = Query(None, ge=1, le=999),
    context: OrchestratorContext = Depends(get_context),
) -> PassPreview:
    """The result line and the clock for values the reviewer has not saved yet (LIVESTACK-53)."""
    return pass_preview.pass_preview(
        context, job_id, prova=prova, car_number=car_number, passagem=passagem
    )


@router.put("/jobs/{job_id}/presence", tags=["review"], response_model=JobPresenceResponse)
def ping_presence(
    job_id: str,
    reviewer: str = Depends(require_reviewer),
    context: OrchestratorContext = Depends(get_context),
) -> JobPresenceResponse:
    """The job page says its reviewer is on this job (LIVESTACK-51); answers who else is."""
    return review_queue.ping_presence(context, reviewer, job_id)


@router.delete("/jobs/{job_id}/presence", tags=["review"], status_code=204)
def leave_presence(
    job_id: str,
    reviewer: str = Depends(require_reviewer),
    context: OrchestratorContext = Depends(get_context),
) -> Response:
    """The job page closed: the reviewer is no longer on this job."""
    context.presence.leave(job_id, reviewer)
    return Response(status_code=204)


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


@router.get("/jobs/{job_id}/original", tags=["review"], response_model=OriginalDownload)
def get_original(
    job_id: str,
    reviewer: str = Depends(require_reviewer),
    context: OrchestratorContext = Depends(get_context),
) -> OriginalDownload:
    """Where "Baixar original" stands (LIVESTACK-41 item 15); no side effects."""
    return originals.get_original(context, job_id)


@router.post("/jobs/{job_id}/original", tags=["review"], response_model=OriginalDownload)
def request_original(
    job_id: str,
    reviewer: str = Depends(require_reviewer),
    context: OrchestratorContext = Depends(get_context),
) -> OriginalDownload:
    """ "Baixar original": ready at once while the inbox has the raw, else a holder uploads it."""
    return originals.request_original(context, job_id, reviewer)


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
    "/jobs/{job_id}/events",
    tags=["events"],
    response_model=EventsResponse,
    dependencies=[Depends(require_reviewer_or_admin)],
)
def job_events(job_id: str, context: OrchestratorContext = Depends(get_context)) -> EventsResponse:
    return event_log.job_events(context, job_id)


@router.get(
    "/events",
    tags=["events"],
    response_model=EventsResponse,
    dependencies=[Depends(require_reviewer_or_admin)],
)
def events(
    worker_id: str | None = Query(None),
    since: datetime | None = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    context: OrchestratorContext = Depends(get_context),
) -> EventsResponse:
    return event_log.recent_events(context, worker_id, since, limit)


@router.get(
    "/fleet",
    tags=["fleet"],
    response_model=FleetResponse,
    dependencies=[Depends(require_reviewer_or_admin)],
)
def get_fleet(context: OrchestratorContext = Depends(get_context)) -> FleetResponse:
    return fleet.build_fleet(context)
