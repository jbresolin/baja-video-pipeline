"""Calls to the orchestrator API, made with the reviewer's own credentials.

The review UI has no database and no account of its own. For every browser
request it builds an OrchestratorClient carrying that browser's
Authorization header, so the orchestrator sees (and checks, and records in
Job.reviewed_by) the actual reviewer.
"""

from urllib.parse import quote

import httpx
from pydantic import ValidationError

from shared.models import (
    ErrorResponse,
    FleetResponse,
    Job,
    JobState,
    JobSummary,
    ManifestResponse,
    RegenerateProxyResponse,
    ReviewQueueResponse,
    ReviewUpdate,
    Team,
)

# The orchestrator's maximum for GET /review/queue?limit=…
QUEUE_LIMIT = 500


class OrchestratorError(Exception):
    """The orchestrator answered with an error status (docs/api.md §3)."""

    def __init__(self, status_code: int, code: str, message: str, details: dict) -> None:
        super().__init__(f"{status_code} {code}: {message}")
        self.status_code = status_code
        # e.g. "version_conflict". A plain string, so a code this UI does not
        # know yet still passes through instead of crashing the parser.
        self.code = code
        self.message = message
        self.details = details


class OrchestratorUnavailableError(Exception):
    """The orchestrator could not be reached, or answered something unreadable."""


def _error_from_response(response: httpx.Response) -> OrchestratorError:
    try:
        body = ErrorResponse.model_validate_json(response.content).error
        return OrchestratorError(response.status_code, body.code, body.message, body.details)
    except ValidationError:
        # Not our error shape (e.g. a proxy's HTML error page).
        text = response.text[:200] or response.reason_phrase
        return OrchestratorError(response.status_code, "upstream_error", text, {})


def _path_segment(value: str | int) -> str:
    """Escape an id for use inside a URL path. Ids are opaque (docs/api.md §1)."""
    return quote(str(value), safe="")


class OrchestratorClient:
    def __init__(self, http_client: httpx.Client, authorization: str | None) -> None:
        # One shared httpx.Client (connection pool) for the whole app; the
        # credentials differ per browser request, so they are added per call.
        self._http_client = http_client
        self._authorization = authorization

    def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        headers = {"Accept": "application/json"}
        if self._authorization:
            headers["Authorization"] = self._authorization
        try:
            response = self._http_client.request(method, path, headers=headers, **kwargs)
        except httpx.HTTPError as error:
            raise OrchestratorUnavailableError(f"orchestrator unreachable: {error}") from error
        if not response.is_success:
            raise _error_from_response(response)
        return response

    def _parse(self, model, response: httpx.Response):
        try:
            return model.model_validate_json(response.content)
        except ValidationError as error:
            raise OrchestratorUnavailableError(
                f"orchestrator sent a {model.__name__} this UI cannot read: {error}"
            ) from error

    def review_queue(self, states: list[JobState]) -> list[JobSummary]:
        # A list of tuples repeats the parameter: ?state=a&state=b.
        params = [("state", state.value) for state in states] + [("limit", QUEUE_LIMIT)]
        response = self._request("GET", "/review/queue", params=params)
        return self._parse(ReviewQueueResponse, response).items

    def get_job(self, job_id: str) -> Job:
        response = self._request("GET", f"/jobs/{_path_segment(job_id)}")
        return self._parse(Job, response)

    def update_review(self, job_id: str, update: ReviewUpdate) -> Job:
        # Fields left as None mean "keep the current value", so they are not sent.
        body = update.model_dump(mode="json", exclude_none=True)
        response = self._request("PATCH", f"/jobs/{_path_segment(job_id)}/review", json=body)
        return self._parse(Job, response)

    def regenerate_proxy(self, job_id: str) -> RegenerateProxyResponse:
        response = self._request("POST", f"/jobs/{_path_segment(job_id)}/regenerate-proxy")
        return self._parse(RegenerateProxyResponse, response)

    def get_team(self, car_number: int) -> Team | None:
        """The team for a car number, or None when the Baja app does not know it."""
        try:
            response = self._request("GET", f"/teams/{_path_segment(car_number)}")
        except OrchestratorError as error:
            if error.status_code == 404:
                return None
            raise
        return self._parse(Team, response)

    def manifest(self) -> ManifestResponse:
        return self._parse(ManifestResponse, self._request("GET", "/manifest"))

    def fleet(self) -> FleetResponse:
        return self._parse(FleetResponse, self._request("GET", "/fleet"))
