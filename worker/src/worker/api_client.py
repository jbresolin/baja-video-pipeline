"""Typed calls to the orchestrator API (docs/api.md §5 and §7.1).

Each method sends one request and returns the parsed shared model, or
raises:
  * OrchestratorUnreachableError: network error or 5xx. Worth retrying later.
  * ApiError: the orchestrator refused (4xx); `code` is the machine-readable
    `error.code` (docs/api.md §3), e.g. "lease_lost", "already_claimed".
"""

from datetime import datetime
from urllib.parse import urljoin, urlsplit

import httpx

from shared.models import (
    ClaimRequest,
    ClaimResponse,
    DeliveredRequest,
    EnrollRequest,
    EnrollResponse,
    FailedRequest,
    HeartbeatRequest,
    HeartbeatResponse,
    HolderConfirmRequest,
    HolderConfirmResponse,
    Job,
    JobStateResponse,
    ManifestResponse,
    NextTaskRequest,
    NextTaskResponse,
    OutboxAckRequest,
    PeersResponse,
    TaskKind,
)
from shared.protocol import LEASE_ID_HEADER


class OrchestratorUnreachableError(Exception):
    """No answer, or a 5xx: the orchestrator (or Caddy) is down or busy."""


class ApiError(Exception):
    def __init__(self, status_code: int, code: str, message: str, details: dict) -> None:
        self.status_code = status_code
        self.code = code
        self.details = details
        super().__init__(f"HTTP {status_code} {code}: {message}")

    @classmethod
    def from_response(cls, response: httpx.Response) -> "ApiError":
        try:
            error = response.json().get("error", {})
        except ValueError:
            error = {}
        return cls(
            response.status_code,
            error.get("code", "unknown"),
            error.get("message", response.text[:200]),
            error.get("details", {}),
        )


class OrchestratorClient:
    def __init__(
        self,
        http: httpx.Client,
        base_url: str,
        *,
        worker_id: str | None = None,
        bearer_token: str | None = None,
    ) -> None:
        self.http = http
        self.base_url = base_url.rstrip("/")
        self.worker_id = worker_id
        self.bearer_token = bearer_token

    # --- Helpers used by tasks too ---

    def absolute_url(self, url: str) -> str:
        """Task offers carry absolute URLs; accept relative ones too, just in case."""
        return urljoin(self.base_url + "/", url)

    def is_orchestrator_url(self, url: str) -> bool:
        """True when `url` points at the orchestrator/Caddy, so it gets our token.

        Peers (port 8710) and the studio must never see the bearer token.
        """
        ours, theirs = urlsplit(self.base_url), urlsplit(self.absolute_url(url))
        return (ours.scheme, ours.netloc) == (theirs.scheme, theirs.netloc)

    def auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.bearer_token}"} if self.bearer_token else {}

    def upload_headers(self, lease_id: str) -> dict[str, str]:
        """Headers for chunked uploads to the orchestrator (proxy, clip)."""
        return {**self.auth_headers(), LEASE_ID_HEADER: lease_id}

    # --- Enrollment (no bearer yet; the one-time token is in the body) ---

    def enroll(self, enroll_base_url: str, request: EnrollRequest) -> EnrollResponse:
        url = enroll_base_url.rstrip("/") + "/enroll"
        response = self._send("POST", url, json=request.model_dump(mode="json"), auth=False)
        return EnrollResponse.model_validate(response.json())

    # --- Worker loop ---

    def heartbeat(self, request: HeartbeatRequest) -> HeartbeatResponse:
        path = f"/workers/{self.worker_id}/heartbeat"
        return HeartbeatResponse.model_validate(self._post_json(path, request).json())

    def next_task(self, accept_kinds: list[TaskKind]) -> NextTaskResponse:
        path = f"/workers/{self.worker_id}/next"
        body = NextTaskRequest(accept_kinds=accept_kinds)
        return NextTaskResponse.model_validate(self._post_json(path, body).json())

    def claim(self, job_id: str, task_kind: TaskKind) -> ClaimResponse:
        body = ClaimRequest(task_kind=task_kind)
        return ClaimResponse.model_validate(self._post_json(f"/jobs/{job_id}/claim", body).json())

    def report_failed(self, job_id: str, request: FailedRequest) -> JobStateResponse:
        response = self._post_json(f"/jobs/{job_id}/failed", request)
        return JobStateResponse.model_validate(response.json())

    def holder_confirm(self, job_id: str, request: HolderConfirmRequest) -> HolderConfirmResponse:
        response = self._post_json(f"/jobs/{job_id}/holder-confirm", request)
        return HolderConfirmResponse.model_validate(response.json())

    def upload_thumbnail(self, url: str, lease_id: str, jpeg_bytes: bytes) -> None:
        headers = {LEASE_ID_HEADER: lease_id, "Content-Type": "image/jpeg"}
        self._send("PUT", url, content=jpeg_bytes, headers=headers)

    def delivered(self, job_id: str, request: DeliveredRequest) -> JobStateResponse:
        response = self._post_json(f"/jobs/{job_id}/delivered", request)
        return JobStateResponse.model_validate(response.json())

    def outbox_ack(self, job_id: str, request: OutboxAckRequest) -> JobStateResponse:
        response = self._post_json(f"/outbox/{job_id}/ack", request)
        return JobStateResponse.model_validate(response.json())

    # --- Lookups ---

    def get_job(self, job_id: str) -> Job:
        return Job.model_validate(self._send("GET", f"/jobs/{job_id}").json())

    def manifest(self, since: datetime | None = None) -> ManifestResponse:
        params = {"since": since.isoformat()} if since else None
        return ManifestResponse.model_validate(self._send("GET", "/manifest", params=params).json())

    def peers(self) -> PeersResponse:
        return PeersResponse.model_validate(self._send("GET", "/workers").json())

    # --- Plumbing ---

    def _post_json(self, path: str, body) -> httpx.Response:
        return self._send("POST", path, json=body.model_dump(mode="json"))

    def _send(
        self,
        method: str,
        url: str,
        *,
        auth: bool = True,
        headers: dict[str, str] | None = None,
        **kwargs,
    ) -> httpx.Response:
        all_headers = {**(self.auth_headers() if auth else {}), **(headers or {})}
        try:
            response = self.http.request(
                method, self.absolute_url(url), headers=all_headers, **kwargs
            )
        except httpx.TransportError as error:
            raise OrchestratorUnreachableError(f"{method} {url}: {error!r}") from error
        if response.status_code >= 500:
            raise OrchestratorUnreachableError(f"{method} {url}: HTTP {response.status_code}")
        if response.status_code >= 400:
            raise ApiError.from_response(response)
        return response
