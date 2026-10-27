"""Typed calls to the orchestrator API (docs/api.md §5 and §7.1).

Each method sends one request and returns the parsed shared model, or
raises:
  * OrchestratorUnreachableError: network error or 5xx. Worth retrying later.
  * ApiError: the orchestrator refused (4xx); `code` is the machine-readable
    `error.code` (docs/api.md §3), e.g. "lease_lost", "already_claimed".
"""

from urllib.parse import urljoin

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
    NextTaskRequest,
    NextTaskResponse,
    OutboxAckRequest,
    TaskKind,
)
from shared.protocol import LEASE_ID_HEADER
from worker.token_origins import may_send_bearer_token


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
        tailnet_base_url: str | None = None,
    ) -> None:
        """`base_url` is worker.json's orchestrator_url; `tailnet_base_url` comes
        from the /enroll answer (None for configs enrolled before it existed)."""
        self.http = http
        self.base_url = base_url.rstrip("/")
        self.worker_id = worker_id
        self.bearer_token = bearer_token
        self.tailnet_base_url = tailnet_base_url

    # --- Helpers used by tasks too ---

    def absolute_url(self, url: str) -> str:
        """Task offers carry absolute URLs; accept relative ones too, just in case."""
        return urljoin(self.base_url + "/", url)

    def auth_headers_for(self, url: str) -> dict[str, str]:
        """The Authorization header for `url`, or nothing.

        Every request that could carry our token asks here, so the rule
        lives in one place (worker.token_origins): only orchestrator_url
        and tailnet_base_url get it; peers and the studio never do.
        """
        if not self.bearer_token:
            return {}
        trusted = (self.base_url, self.tailnet_base_url)
        if not may_send_bearer_token(self.absolute_url(url), trusted):
            return {}
        return {"Authorization": f"Bearer {self.bearer_token}"}

    def upload_headers(self, url: str, lease_id: str) -> dict[str, str]:
        """Headers for chunked uploads to the orchestrator (proxy, clip)."""
        return {**self.auth_headers_for(url), LEASE_ID_HEADER: lease_id}

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
        absolute_url = self.absolute_url(url)
        auth_headers = self.auth_headers_for(absolute_url) if auth else {}
        all_headers = {**auth_headers, **(headers or {})}
        try:
            response = self.http.request(method, absolute_url, headers=all_headers, **kwargs)
        except httpx.TransportError as error:
            raise OrchestratorUnreachableError(f"{method} {url}: {error!r}") from error
        if response.status_code >= 500:
            raise OrchestratorUnreachableError(f"{method} {url}: HTTP {response.status_code}")
        if response.status_code >= 400:
            raise ApiError.from_response(response)
        return response
