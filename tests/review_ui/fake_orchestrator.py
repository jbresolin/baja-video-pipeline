"""An in-memory stand-in for the orchestrator API, for the review UI tests.

It implements only the endpoints the review UI calls, with the behaviour
docs/api.md describes:

    GET   /review/queue?state=…&limit=…
    GET   /review/car-videos?prova=…&car_number=…
    GET   /jobs/{job_id}
    GET   /jobs/{job_id}/sender-messages   (LIVESTACK-49)
    PATCH /jobs/{job_id}/review          (expected_version, actions, 409/422;
                                          show_pass_result updates pass_result.show)
    POST  /jobs/{job_id}/regenerate-proxy
    GET   /jobs/{job_id}/original, POST /jobs/{job_id}/original
    GET   /teams/{car_number}
    GET   /manifest
    GET   /fleet
    GET   /jobs/{job_id}/events
    GET   /events?worker_id=…&since=…&limit=…
    POST  /auth/login, /auth/logout, GET /auth/me   (sign-in)
    GET   /provas

Like the real one, it answers 401 unless the request carries the
reviewer's session token (REVIEWER_AUTHORIZATION); only /auth/login is
open. It also writes down every request it receives, so a test can check
exactly what the UI sent. The admin pages are tested against the real
orchestrator instead (test_admin_pages.py).

The UI talks to it through httpx + SyncAsgiTransport: real HTTP requests
and responses, but handled in memory, with no network or ports.
"""

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import httpx
from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse, Response
from pydantic import ValidationError

from shared.layout import slugify_prova
from shared.models import (
    Event,
    FleetResponse,
    Job,
    JobState,
    JobSummary,
    ManifestResponse,
    PresentReviewer,
    Prova,
    ReviewAction,
    ReviewUpdate,
    SenderMessage,
    Team,
)
from shared.state_machine import DISCARDABLE_STATES, REPROCESSABLE_STATES, RETRYABLE_STATES
from shared.testing.asgi_transport import SyncAsgiTransport
from tests.review_ui.sample_data import (
    REVIEWER_AUTHORIZATION,
    REVIEWER_PASSWORD,
    REVIEWER_SESSION_TOKEN,
    REVIEWER_USERNAME,
    make_fleet,
    make_manifest,
    make_provas,
    make_teams,
)
from tests.review_ui.sample_events import make_events

# Which states each review action is accepted from (docs/api.md §7.1).
ACTION_FROM_STATES = {
    ReviewAction.READY: {JobState.AWAITING_REVIEW},
    ReviewAction.REPROCESS: REPROCESSABLE_STATES,
    ReviewAction.DISCARD: DISCARDABLE_STATES,
    ReviewAction.RETRY: RETRYABLE_STATES,
}
ACTION_TO_STATE = {
    ReviewAction.READY: JobState.READY,
    ReviewAction.REPROCESS: JobState.READY,
    ReviewAction.RETRY: JobState.READY,
    ReviewAction.DISCARD: JobState.DISCARDED,
}
REQUIRED_FIELDS = ["prova", "car_number", "passagem", "cut_in_s", "cut_out_s"]
# GET /events: default and largest `limit` (docs/api.md §7.1).
EVENTS_DEFAULT_LIMIT = 100
EVENTS_MAX_LIMIT = 1000


@dataclass
class RecordedRequest:
    method: str
    path: str
    # Query parameters as (name, value) pairs, so repeated ones stay visible.
    query: list[tuple[str, str]]
    headers: dict[str, str]
    json_body: dict | None


def _error(status: int, code: str, message: str, **details) -> JSONResponse:
    return JSONResponse(
        {"error": {"code": code, "message": message, "details": details}}, status_code=status
    )


@dataclass
class FakeOrchestrator:
    jobs: dict[str, Job] = field(default_factory=dict)
    teams: dict[int, Team] = field(default_factory=make_teams)
    manifest: ManifestResponse = field(default_factory=make_manifest)
    fleet: FleetResponse = field(default_factory=make_fleet)
    # The event log, oldest first (ids count up).
    events: list[Event] = field(default_factory=make_events)
    provas: list[Prova] = field(default_factory=make_provas)
    # Job id → its "Mensagens de <remetente>" items, already in display
    # order (the orchestrator decides roles and order; the fake just keeps them).
    sender_messages: dict[str, list[SenderMessage]] = field(default_factory=dict)
    # Job id → the *other* reviewers on it (LIVESTACK-51): what PUT
    # …/presence answers and what the queue lists in `reviewing`.
    presence: dict[str, list[PresentReviewer]] = field(default_factory=dict)
    requests: list[RecordedRequest] = field(default_factory=list)
    # Switches that make an endpoint fail, to test the UI's error handling.
    fleet_fails: bool = False
    car_videos_fail: bool = False
    teams_upstream_down: bool = False
    # Makes both event endpoints answer 500.
    events_fail: bool = False
    sender_messages_fail: bool = False
    presence_fails: bool = False

    def add_job(self, job: Job) -> None:
        self.jobs[job.id] = job

    def summary(self, job: Job) -> dict:
        """JobSummary as the lists send it, with the message count (LIVESTACK-49)."""
        summary = JobSummary.from_job(job)
        count = len(self.sender_messages.get(job.id, []))
        # The sender for the "Enviado por" filter (LIVESTACK-51): the JID's
        # digits and, with no contacts here, the WhatsApp name.
        jid = job.source.sender_jid
        update = {
            "sender_message_count": count,
            "reviewing": [
                reviewer.model_dump(mode="json") for reviewer in self.presence.get(job.id, [])
            ],
            "sender_phone": jid.split("@", 1)[0] if jid else None,
            "sender_display_name": job.source.sender_name,
        }
        return summary.model_copy(update=update).model_dump(mode="json")

    def http_client(self) -> httpx.Client:
        """An httpx.Client whose requests are answered by this fake, in memory."""
        return httpx.Client(transport=SyncAsgiTransport(build_app(self)), base_url="http://orch")

    def select_events(
        self, worker_id: str | None, since: datetime | None, limit: int
    ) -> list[Event]:
        """GET /events semantics (docs/api.md §7.1), always oldest first.

        With `since`: the first `limit` events strictly after it (paging
        forward). Without: the newest `limit` events.
        """
        matching = sorted(self.events, key=lambda event: event.id)
        if worker_id is not None:
            matching = [event for event in matching if event.worker_id == worker_id]
        if since is not None:
            return [event for event in matching if event.at > since][:limit]
        return matching[-limit:]

    def requests_to(self, method: str, path: str) -> list[RecordedRequest]:
        return [r for r in self.requests if r.method == method and r.path == path]

    def apply_review(self, job_id: str, update: ReviewUpdate, reviewer: str) -> JSONResponse:
        job = self.jobs.get(job_id)
        if job is None:
            return _error(404, "not_found", f"no job {job_id}")
        if update.expected_version != job.version:
            return _error(
                409,
                "version_conflict",
                f"job is at version {job.version}, you sent {update.expected_version}",
                current_version=job.version,
            )
        allowed = ACTION_FROM_STATES.get(update.action)
        save_allowed = update.action == ReviewAction.SAVE and job.state != JobState.DISCARDED
        if not save_allowed and (allowed is None or job.state not in allowed):
            return _error(
                409,
                "illegal_transition",
                f"cannot {update.action.value} a job in {job.state.value}",
                current=job.state.value,
                requested=update.action.value,
            )
        # Fields left out (None) keep their current value.
        changes = update.model_dump(
            exclude_none=True, exclude={"expected_version", "action", "clear_largada"}
        )
        if update.clear_largada:
            # Like the orchestrator (LIVESTACK-48): the flag removes the mark.
            changes["largada_s"] = None
        if "car_number" in changes and changes["car_number"] in self.teams:
            changes["team_name"] = self.teams[changes["car_number"]].team_name
        updated = job.model_copy(update=changes)
        if update.show_pass_result is not None and job.pass_result is not None:
            # Like the orchestrator (LIVESTACK-47): the toggle in force follows
            # the reviewer's choice, for a prova that shows results at all.
            shown = job.pass_result.configured and update.show_pass_result
            updated = updated.model_copy(
                update={"pass_result": job.pass_result.model_copy(update={"show": shown})}
            )
        needs_fields = update.action in {
            ReviewAction.READY,
            ReviewAction.REPROCESS,
            ReviewAction.RETRY,
        }
        missing = [name for name in REQUIRED_FIELDS if getattr(updated, name) is None]
        if needs_fields and missing:
            return _error(422, "missing_review_fields", "fields missing", missing=missing)
        final_changes = {
            "version": job.version + 1,
            "updated_at": job.updated_at + timedelta(minutes=1),
            "reviewed_by": reviewer,
            "reviewed_at": job.updated_at + timedelta(minutes=1),
        }
        if update.action in ACTION_TO_STATE:
            final_changes["state"] = ACTION_TO_STATE[update.action]
        if update.action == ReviewAction.RETRY:
            final_changes["failure"] = None
        self.jobs[job_id] = updated.model_copy(update=final_changes)
        return JSONResponse(self.jobs[job_id].model_dump(mode="json"))


def filming_order(job: Job) -> tuple:
    """The orchestrator's order: recording time, then arrival, then id."""
    return (job.recorded_at or job.created_at, job.created_at, job.id)


def _reviewer_from(request: Request) -> str | None:
    """The reviewer's user name if the request carries the accepted session token."""
    if request.headers.get("authorization") != REVIEWER_AUTHORIZATION:
        return None
    return REVIEWER_USERNAME


def build_app(fake: FakeOrchestrator) -> FastAPI:
    app = FastAPI()

    @app.middleware("http")
    async def record_and_check_auth(request: Request, call_next):
        body = await request.body()
        fake.requests.append(
            RecordedRequest(
                method=request.method,
                path=request.url.path,
                query=list(request.query_params.multi_items()),
                headers=dict(request.headers),
                json_body=json.loads(body) if body else None,
            )
        )
        if request.url.path != "/auth/login" and _reviewer_from(request) is None:
            return _error(401, "unauthenticated", "reviewer credentials required")
        return await call_next(request)

    @app.post("/auth/login")
    async def login(request: Request):
        body = json.loads(await request.body())
        if (body.get("username"), body.get("password")) != (REVIEWER_USERNAME, REVIEWER_PASSWORD):
            return _error(401, "unauthenticated", "wrong username or password")
        user = {"username": REVIEWER_USERNAME, "role": "reviewer"}
        return {"session_token": REVIEWER_SESSION_TOKEN, "user": user, "idle_timeout_s": 43200}

    @app.post("/auth/logout", status_code=204)
    def logout():
        return None

    @app.get("/auth/me")
    def me():
        return {"username": REVIEWER_USERNAME, "role": "reviewer"}

    @app.get("/provas")
    def provas():
        return {"provas": [prova.model_dump(mode="json") for prova in fake.provas]}

    @app.get("/review/queue")
    def review_queue(
        state: list[JobState] = Query([JobState.AWAITING_REVIEW]),
        limit: int = 100,
        outdated: bool = False,
    ):
        matching = [job for job in fake.jobs.values() if job.state in state]
        if outdated:
            # Like the orchestrator: only jobs whose clip no longer matches the review.
            matching = [job for job in matching if JobSummary.from_job(job).clip_outdated]
        # Filming order, like the orchestrator (LIVESTACK-45).
        oldest_first = sorted(matching, key=filming_order)[:limit]
        return {"items": [fake.summary(job) for job in oldest_first]}

    @app.get("/review/car-videos")
    def car_videos(prova: str, car_number: int):
        if fake.car_videos_fail:
            return _error(500, "internal", "car videos unavailable")
        # Like the orchestrator: same clip folder (slug) and car, not
        # discarded, in filming order.
        slug = slugify_prova(prova)
        matching = [
            job
            for job in fake.jobs.values()
            if job.prova
            and slugify_prova(job.prova) == slug
            and job.car_number == car_number
            and job.state != JobState.DISCARDED
        ]
        items = [fake.summary(job) for job in sorted(matching, key=filming_order)]
        return {"prova_slug": slug, "car_number": car_number, "items": items}

    @app.get("/jobs/{job_id}")
    def get_job(job_id: str):
        if job_id not in fake.jobs:
            return _error(404, "not_found", f"no job {job_id}")
        return fake.jobs[job_id].model_dump(mode="json")

    @app.put("/jobs/{job_id}/presence")
    def ping_presence(job_id: str):
        if fake.presence_fails:
            return _error(500, "internal_error", "presence is down for this test")
        if job_id not in fake.jobs:
            return _error(404, "not_found", f"no job {job_id}")
        others = fake.presence.get(job_id, [])
        return {"others": [reviewer.model_dump(mode="json") for reviewer in others]}

    @app.delete("/jobs/{job_id}/presence", status_code=204)
    def leave_presence(job_id: str):
        return Response(status_code=204)

    @app.get("/jobs/{job_id}/sender-messages")
    def sender_messages(job_id: str):
        if fake.sender_messages_fail:
            return _error(500, "internal_error", "messages are down for this test")
        if job_id not in fake.jobs:
            return _error(404, "not_found", f"no job {job_id}")
        items = fake.sender_messages.get(job_id, [])
        return {
            "job_id": job_id,
            "sender_name": fake.jobs[job_id].source.sender_name,
            "items": [item.model_dump(mode="json") for item in items],
        }

    @app.patch("/jobs/{job_id}/review")
    async def update_review(job_id: str, request: Request):
        try:
            update = ReviewUpdate.model_validate_json(await request.body())
        except ValidationError as error:
            return _error(400, "validation_error", str(error))
        return fake.apply_review(job_id, update, _reviewer_from(request) or "")

    @app.post("/jobs/{job_id}/regenerate-proxy", status_code=202)
    def regenerate_proxy(job_id: str):
        if job_id not in fake.jobs:
            return _error(404, "not_found", f"no job {job_id}")
        return {"job_id": job_id, "queued": True, "waiting_reason": "no holder is online"}

    @app.post("/jobs/{job_id}/original")
    def request_original(job_id: str):
        if job_id not in fake.jobs:
            return _error(404, "not_found", f"no job {job_id}")
        name = f"voluntario-20261025-090000-{job_id[:8]}.mp4"
        return {
            "state": "ready",
            "download_url": f"/original-downloads/{job_id}/{name}",
            "file_name": name,
            "size_bytes": 10,
            "expires_at": "2026-10-26T12:00:00Z",
        }

    @app.get("/jobs/{job_id}/original")
    def original_status(job_id: str):
        return {"state": "preparing"}

    @app.get("/teams/{car_number}")
    def get_team(car_number: int):
        if fake.teams_upstream_down:
            return _error(503, "upstream_error", "the Baja app did not answer")
        if car_number not in fake.teams:
            return _error(404, "not_found", f"no team with car {car_number}")
        return fake.teams[car_number].model_dump(mode="json")

    @app.get("/manifest")
    def manifest():
        return fake.manifest.model_dump(mode="json")

    @app.get("/fleet")
    def fleet():
        if fake.fleet_fails:
            return _error(503, "upstream_error", "fleet is down for this test")
        return fake.fleet.model_dump(mode="json")

    @app.get("/jobs/{job_id}/events")
    def job_events(job_id: str):
        if fake.events_fail:
            return _error(500, "internal_error", "event log is down for this test")
        if job_id not in fake.jobs:
            return _error(404, "not_found", f"no job {job_id}")
        ordered = sorted(fake.events, key=lambda event: event.id)
        return {"events": [e.model_dump(mode="json") for e in ordered if e.job_id == job_id]}

    @app.get("/events")
    def events(
        worker_id: str | None = None,
        since: datetime | None = None,
        limit: int = Query(EVENTS_DEFAULT_LIMIT, ge=1, le=EVENTS_MAX_LIMIT),
    ):
        if fake.events_fail:
            return _error(500, "internal_error", "event log is down for this test")
        selected = fake.select_events(worker_id, since, limit)
        return {"events": [event.model_dump(mode="json") for event in selected]}

    return app
