"""Contract: review UI endpoints, delivery (manifest, outbox ACK) and fleet view.

Outbox *bytes* are served by Caddy (/outbox/* on the tailnet listener,
guarded by GET /auth/file), not by the orchestrator (design §15).
"""

from datetime import datetime

from fastapi import APIRouter, Depends, Query, Response

from shared.models import (
    CarVideosResponse,
    ClipDownload,
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
from tools.openapi_contract.auth import (
    admin_bearer,
    error_responses,
    not_implemented,
    session_bearer,
    worker_bearer,
)

router = APIRouter()

# Two dependencies on one route mean "either credential is accepted".
# A session of a reviewer or an admin counts as a reviewer here.
_reviewer_only = [Depends(session_bearer)]
_reviewer_or_worker = [Depends(session_bearer), Depends(worker_bearer)]
_reviewer_or_admin = [Depends(session_bearer), Depends(admin_bearer)]


# --- Review ---


@router.get(
    "/review/queue",
    tags=["review"],
    summary="Jobs waiting for review, in filming order",
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
    outdated: bool = Query(
        False,
        description="Only jobs whose newest clip no longer matches the review "
        "(JobSummary.clip_outdated), e.g. ?outdated=true&state=done",
    ),
) -> ReviewQueueResponse:
    """Oldest recording first (`JobSummary.recorded_at`), ties by arrival
    (LIVESTACK-45): arrival order is not filming order."""
    not_implemented()


@router.get(
    "/review/car-videos",
    tags=["review"],
    summary="One car's videos in one prova, in filming order",
    response_model=CarVideosResponse,
    dependencies=_reviewer_only,
    responses=error_responses(400, 401),
)
def car_videos(
    prova: str = Query(
        ...,
        min_length=1,
        max_length=200,
        description="The prova's name or slug. Jobs are matched by the prova's clip "
        "folder (slug), so a renamed prova still finds every video.",
    ),
    car_number: int = Query(..., ge=0, le=9999),
) -> CarVideosResponse:
    """For the job page's "Outros vídeos de <prova>, carro <n>" panel
    (LIVESTACK-45): every job of that car in that prova, discarded ones
    left out, oldest recording first (ties by arrival). The asking job is
    included when it is saved with that prova and car; the review UI places
    it among the others and works out the passagem suggestion and warnings."""
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


@router.get(
    "/jobs/{job_id}/sender-messages",
    tags=["review"],
    summary="The sender's messages around this video (job page)",
    response_model=SenderMessagesResponse,
    dependencies=_reviewer_only,
    responses=error_responses(401, 404),
)
def get_sender_messages(job_id: str) -> SenderMessagesResponse:
    """For the job page's "Mensagens de <remetente>" (LIVESTACK-49), in
    display order: the video's caption; the sender's messages that quote
    this video (role `reply`, whenever they were sent); those received
    after it and before the sender's next video (`after`); those received
    after the sender's previous video and before this one (`before`). A
    message quoting another of the sender's videos is listed only there.
    Oldest first within each role. Texts and captions only; other messages
    by kind (`audio`, `image`, ...). Edited messages carry the new text and
    `edited: true`; deleted ones `deleted: true` and no text. Messages are
    kept for the Settings' `sender_messages_keep_days` (14)."""
    not_implemented()


@router.get(
    "/jobs/{job_id}/pass-preview",
    tags=["review"],
    summary="The result line and the clock for prova, car and passagem not saved yet",
    response_model=PassPreview,
    dependencies=_reviewer_only,
    responses=error_responses(400, 401, 404),
)
def get_pass_preview(
    job_id: str,
    prova: str | None = Query(
        None, min_length=1, max_length=64, description="Left out = the job's saved prova."
    ),
    car_number: int | None = Query(
        None, ge=0, le=9999, description="Left out = the job's saved car number."
    ),
    passagem: int | None = Query(
        None, ge=1, le=999, description="Left out = the job's saved passagem."
    ),
) -> PassPreview:
    """For the job page while the reviewer types (LIVESTACK-53): exactly
    what `Job.pass_result` and `Job.pass_clock` would be after saving these
    values (the same functions, on the saved job with these values put in),
    so "Resultado da passagem" and "Cronômetro" appear, change and go
    without a save. A value left out keeps the saved one, as in PATCH
    …/review. Nothing is stored on the job; the toggles and the largada are
    the saved ones (the page reads its own fields for those). A prova whose
    results table was never fetched (its first video) gets it fetched now,
    as "Testar" does: at most 3 s, not again within 30 s of the table's
    last try; on failure the answer says `not_fetched`."""
    not_implemented()


@router.put(
    "/jobs/{job_id}/presence",
    tags=["review"],
    summary="The job page is open: say so, and learn who else has it open",
    response_model=JobPresenceResponse,
    dependencies=_reviewer_only,
    responses=error_responses(401, 404),
)
def ping_presence(job_id: str) -> JobPresenceResponse:
    """Sent by the job page on opening and every ~20 s while it stays open
    (LIVESTACK-51). Answers the *other* reviewers on this job (the caller
    left out), first arrived first, with when each one's first ping came.
    A reviewer with no ping for 45 s is gone: nothing is ever locked, this
    only informs. In memory: a restart forgets it until the next pings.
    `GET /review/queue` lists everyone on each job in `JobSummary.reviewing`."""
    not_implemented()


@router.delete(
    "/jobs/{job_id}/presence",
    tags=["review"],
    summary="The job page closed",
    status_code=204,
    response_class=Response,
    dependencies=_reviewer_only,
    responses=error_responses(401),
)
def leave_presence(job_id: str) -> Response:
    """Ends the caller's presence on this job at once (instead of after 45 s).
    Always 204, also when there was none."""
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
    "/jobs/{job_id}/clip-download",
    tags=["review"],
    summary='Where "Baixar clipe" stands (LIVESTACK-52)',
    response_model=ClipDownload,
    dependencies=_reviewer_only,
    responses=error_responses(401, 404),
)
def get_clip_download(job_id: str) -> ClipDownload:
    """No side effects. `ready` carries the link ("/requested-clips/<job_id>/
    <readable clip name>", relative to the public base URL) and when the
    VPS copy goes: 30 minutes after the first complete download, else 24 h
    after the request (Settings)."""
    not_implemented()


@router.post(
    "/jobs/{job_id}/clip-download",
    tags=["review"],
    summary='"Baixar clipe": make the newest clip downloadable for a while',
    response_model=ClipDownload,
    dependencies=_reviewer_only,
    responses=error_responses(401, 404, 409),
)
def request_clip_download(job_id: str) -> ClipDownload:
    """Always the job's newest clip version (409 conflict without a clip).
    Ready at once while the VPS outbox has exactly that clip; otherwise a
    PC that has it is offered `upload_clip`: the master (archive) first,
    then the worker that rendered it, the studio only as a last resort and
    not while it is delivering; only workers declaring the render feature
    `upload_clip`. Asking again restarts the times."""
    not_implemented()


@router.get(
    "/jobs/{job_id}/original",
    tags=["review"],
    summary='Where "Baixar original" stands (LIVESTACK-41 item 15)',
    response_model=OriginalDownload,
    dependencies=_reviewer_only,
    responses=error_responses(401, 404),
)
def get_original(job_id: str) -> OriginalDownload:
    """No side effects. `ready` comes with `download_url`
    ("/original-downloads/<job_id>/<readable raw name>", relative to the
    public base URL, reviewer auth like proxies) and `expires_at`."""
    not_implemented()


@router.post(
    "/jobs/{job_id}/original",
    tags=["review"],
    summary='"Baixar original": make the raw downloadable for a day',
    response_model=OriginalDownload,
    dependencies=_reviewer_only,
    responses=error_responses(401, 404),
)
def request_original(job_id: str) -> OriginalDownload:
    """While the VPS inbox still has the raw the answer is `ready` at once.
    Otherwise a holder (the master first) is offered `upload_original`:
    `preparing`, or `no_holder_online` when none is online. Asking again
    restarts the keep time (Settings `originals_keep_hours`)."""
    not_implemented()


@router.get(
    "/teams/{car_number}",
    tags=["review"],
    summary="Team for a car number, from the teams table (results API sync + corrections)",
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
