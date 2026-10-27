"""An in-memory stand-in for the orchestrator API, for the review UI tests.

It implements only the endpoints the review UI calls, with the behaviour
docs/api.md describes:

    GET   /review/queue?state=…&limit=…
    GET   /jobs/{job_id}
    PATCH /jobs/{job_id}/review          (expected_version, actions, 409/422)
    POST  /jobs/{job_id}/regenerate-proxy
    GET   /teams/{car_number}
    GET   /manifest
    GET   /fleet

Like the real one, it answers 401 unless the request carries the reviewer's
Basic credentials (REVIEWER_AUTHORIZATION). It also writes down every
request it receives, so a test can check exactly what the UI sent.

The UI talks to it through httpx + SyncAsgiTransport: real HTTP requests
and responses, but handled in memory, with no network or ports.
"""

import base64
import json
from dataclasses import dataclass, field
from datetime import timedelta

import httpx
from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from shared.models import (
    FleetResponse,
    Job,
    JobState,
    JobSummary,
    ManifestResponse,
    ReviewAction,
    ReviewUpdate,
    Team,
)
from shared.state_machine import DISCARDABLE_STATES, REPROCESSABLE_STATES, RETRYABLE_STATES
from shared.testing.asgi_transport import SyncAsgiTransport
from tests.review_ui.sample_data import (
    REVIEWER_AUTHORIZATION,
    make_fleet,
    make_manifest,
    make_teams,
)

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
    requests: list[RecordedRequest] = field(default_factory=list)
    # Switches that make an endpoint fail, to test the UI's error handling.
    fleet_fails: bool = False
    teams_upstream_down: bool = False

    def add_job(self, job: Job) -> None:
        self.jobs[job.id] = job

    def http_client(self) -> httpx.Client:
        """An httpx.Client whose requests are answered by this fake, in memory."""
        return httpx.Client(transport=SyncAsgiTransport(build_app(self)), base_url="http://orch")

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
        changes = update.model_dump(exclude_none=True, exclude={"expected_version", "action"})
        if "car_number" in changes and changes["car_number"] in self.teams:
            changes["team_name"] = self.teams[changes["car_number"]].team_name
        updated = job.model_copy(update=changes)
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


def _reviewer_from(request: Request) -> str | None:
    """The reviewer's user name if the Basic credentials are the accepted ones."""
    if request.headers.get("authorization") != REVIEWER_AUTHORIZATION:
        return None
    encoded = REVIEWER_AUTHORIZATION.removeprefix("Basic ")
    return base64.b64decode(encoded).decode().partition(":")[0]


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
        if _reviewer_from(request) is None:
            return _error(401, "unauthenticated", "reviewer credentials required")
        return await call_next(request)

    @app.get("/review/queue")
    def review_queue(state: list[JobState] = Query([JobState.AWAITING_REVIEW]), limit: int = 100):
        matching = [job for job in fake.jobs.values() if job.state in state]
        oldest_first = sorted(matching, key=lambda job: job.created_at)[:limit]
        items = [JobSummary.from_job(job).model_dump(mode="json") for job in oldest_first]
        return {"items": items}

    @app.get("/jobs/{job_id}")
    def get_job(job_id: str):
        if job_id not in fake.jobs:
            return _error(404, "not_found", f"no job {job_id}")
        return fake.jobs[job_id].model_dump(mode="json")

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

    return app
