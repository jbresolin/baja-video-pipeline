"""An in-memory stand-in for the orchestrator + Caddy, for worker tests.

It implements only the endpoints the worker calls (docs/api.md §7.1),
plus Caddy's /inbox/* and /outbox/* file serving, and remembers every
request so tests can check what the worker sent. The harness routes both
BASE_URL (the worker's orchestrator_url) and TAILNET_URL (the
tailnet_base_url from /enroll) to this one app, as on the VPS where both
are Caddy in front of the orchestrator. Tests drive it directly:

    orchestrator.add_job(raw_bytes)          a job whose raw sits in the inbox
    orchestrator.offer(task)                 what /next will hand out
    orchestrator.revoke_lease(lease_id)      the next heartbeat says "lost"

It is called through shared.testing.SyncAsgiTransport, so no network or
port is involved. Answers use the real shared models and the real
ChunkedUploadReceiver, so a worker that passes here speaks the contract.
"""

import threading
import uuid
from datetime import timedelta
from pathlib import Path

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

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
    Lease,
    LeaseHeartbeatStatus,
    NextTaskRequest,
    NextTaskResponse,
    OutboxAckRequest,
    WorkerRole,
    utc_now,
)
from shared.protocol import FRAME_RATE_HEADER
from tests.worker.fake_orchestrator_parts import FakeJob, error, range_response, receive_chunk

BASE_URL = "http://orchestrator.test:8080"
# Caddy's tailnet listener as /enroll reports it; a second origin that may
# see the bearer token (design §17).
TAILNET_URL = "http://tailnet.test:8080"
WORKER_ID = "worker-1"
BEARER_TOKEN = "secret-worker-token"
GOOD_ENROLLMENT_TOKEN = "good-enrollment-token"


class FakeOrchestrator:
    def __init__(self, data_dir: Path, role: WorkerRole = WorkerRole.WORKER) -> None:
        self.data_dir = data_dir
        self.role = role
        self.jobs: dict[str, FakeJob] = {}
        self.offers: list = []
        self.leases: dict[str, Lease] = {}
        self.revoked_lease_ids: set[str] = set()
        self.inbox: dict[str, bytes] = {}
        self.outbox: dict[str, bytes] = {}
        # Knobs tests turn:
        self.accepting_new_raws = True
        self.deletable_job_ids: list[str] = []
        self.heartbeats_to_fail = 0
        self.delivered_calls_to_fail = 0
        # (status, code) to refuse every /delivered with, e.g. (409, "illegal_transition").
        self.delivered_refusal: tuple[int, str] | None = None
        self.refuse_claims_with: str | None = None
        # Called as (kind, received_bytes) after every accepted upload chunk.
        self.after_chunk_accepted = None
        # What the worker sent:
        self.heartbeats: list[HeartbeatRequest] = []
        self.next_requests: list[NextTaskRequest] = []
        self.holder_confirms: list[tuple[str, HolderConfirmRequest]] = []
        self.failures: list[tuple[str, FailedRequest]] = []
        self.thumbnails: dict[str, bytes] = {}
        self.upload_log: list[tuple[str, int, int]] = []  # (endpoint, status, offset)
        # The Frame-Rate header of every proxy chunk, None where it was absent.
        self.proxy_frame_rate_headers: list[str | None] = []
        self.delivered: list[tuple[str, DeliveredRequest]] = []
        self.outbox_acks: list[tuple[str, OutboxAckRequest]] = []
        self.file_requests: list[tuple[str, dict[str, str]]] = []  # (path, headers)
        self.enroll_requests: list[EnrollRequest] = []
        self._lock = threading.Lock()
        self.app = self._build_app()

    # --- Test helpers ---

    def add_job(self, raw_bytes: bytes, **fields) -> FakeJob:
        job = FakeJob(job_id=uuid.uuid4().hex, raw_bytes=raw_bytes, **fields)
        self.jobs[job.job_id] = job
        self.inbox[f"{job.job_id}.mp4"] = raw_bytes
        return job

    def offer(self, task) -> None:
        self.offers.append(task)

    def revoke_lease(self, lease_id: str) -> None:
        """Like a reviewer's reprocess: the lease is gone; heartbeats say lost."""
        self.leases.pop(lease_id, None)
        self.revoked_lease_ids.add(lease_id)

    def lease_for(self, job_id: str) -> Lease:
        return next(lease for lease in self.leases.values() if lease.job_id == job_id)

    def stored_upload(self, kind: str, job_id: str) -> Path:
        """Where /jobs/{id}/proxy ("proxies") or /clip ("outbox") uploads land."""
        return self.data_dir / kind / f"{job_id}.mp4"

    # --- The app ---

    def _build_app(self) -> Starlette:
        routes = [
            Route("/enroll", self._enroll, methods=["POST"]),
            Route("/workers/{worker_id}/heartbeat", self._heartbeat, methods=["POST"]),
            Route("/workers/{worker_id}/next", self._next, methods=["POST"]),
            Route("/jobs/{job_id}", self._get_job, methods=["GET"]),
            Route("/jobs/{job_id}/claim", self._claim, methods=["POST"]),
            Route("/jobs/{job_id}/holder-confirm", self._holder_confirm, methods=["POST"]),
            Route("/jobs/{job_id}/thumbnail", self._thumbnail, methods=["PUT"]),
            Route("/jobs/{job_id}/proxy", self._chunk_endpoint("proxies"), methods=["POST"]),
            Route("/jobs/{job_id}/clip", self._chunk_endpoint("outbox"), methods=["POST"]),
            Route("/jobs/{job_id}/failed", self._failed, methods=["POST"]),
            Route("/jobs/{job_id}/delivered", self._delivered, methods=["POST"]),
            Route("/outbox/{job_id}/ack", self._outbox_ack, methods=["POST"]),
            Route("/inbox/{name}", self._file_endpoint(self.inbox), methods=["GET"]),
            Route("/outbox/{name}", self._file_endpoint(self.outbox), methods=["GET"]),
        ]
        return Starlette(routes=routes)

    @staticmethod
    def _authorized(request: Request) -> bool:
        return request.headers.get("authorization") == f"Bearer {BEARER_TOKEN}"

    def _end_lease(self, lease_id: str) -> None:
        self.leases.pop(lease_id, None)

    def _lease_is_alive(self, lease_id: str) -> bool:
        return lease_id in self.leases

    async def _enroll(self, request: Request) -> Response:
        body = EnrollRequest.model_validate(await request.json())
        self.enroll_requests.append(body)
        if body.enrollment_token != GOOD_ENROLLMENT_TOKEN:
            return error(401, "enrollment_token_invalid")
        answer = EnrollResponse(
            worker_id=WORKER_ID,
            role=self.role,
            bearer_token=BEARER_TOKEN,
            tailscale_auth_key="tskey-auth-test",
            tailscale_tags=[f"tag:{'studio' if self.role == WorkerRole.STUDIO else 'worker'}"],
            tailnet_base_url=TAILNET_URL,
            heartbeat_interval_s=15,
            lease_ttl_s=45,
        )
        return JSONResponse(answer.model_dump(mode="json"), status_code=201)

    async def _heartbeat(self, request: Request) -> Response:
        if not self._authorized(request):
            return error(401, "unauthenticated")
        if self.heartbeats_to_fail > 0:
            self.heartbeats_to_fail -= 1
            return error(503, "upstream_error")
        body = HeartbeatRequest.model_validate(await request.json())
        self.heartbeats.append(body)
        statuses = {
            lease_id: LeaseHeartbeatStatus.RENEWED
            if self._lease_is_alive(lease_id)
            else LeaseHeartbeatStatus.LOST
            for lease_id in body.current_lease_ids
        }
        answer = HeartbeatResponse(
            lease_status=statuses,
            heartbeat_interval_s=15,
            accepting_new_raws=self.accepting_new_raws,
            deletable_job_ids=[j for j in self.deletable_job_ids if j in body.held_job_ids],
        )
        return JSONResponse(answer.model_dump(mode="json"))

    async def _next(self, request: Request) -> Response:
        body = NextTaskRequest.model_validate(await request.json())
        self.next_requests.append(body)
        task = next((t for t in self.offers if t.kind in body.accept_kinds), None)
        answer = NextTaskResponse(task=task, retry_after_s=1)
        return JSONResponse(answer.model_dump(mode="json"))

    async def _claim(self, request: Request) -> Response:
        job_id = request.path_params["job_id"]
        body = ClaimRequest.model_validate(await request.json())
        if self.refuse_claims_with:
            return error(409, self.refuse_claims_with)
        task = next(
            (t for t in self.offers if t.job_id == job_id and t.kind == body.task_kind), None
        )
        if task is None:
            return error(409, "not_claimable")
        self.offers.remove(task)
        now = utc_now()
        lease = Lease(
            lease_id=uuid.uuid4().hex,
            job_id=job_id,
            worker_id=WORKER_ID,
            task_kind=body.task_kind,
            acquired_at=now,
            expires_at=now + timedelta(seconds=45),
        )
        self.leases[lease.lease_id] = lease
        return JSONResponse(ClaimResponse(lease=lease, task=task).model_dump(mode="json"))

    async def _holder_confirm(self, request: Request) -> Response:
        job_id = request.path_params["job_id"]
        body = HolderConfirmRequest.model_validate(await request.json())
        if not self._lease_is_alive(body.lease_id):
            return error(409, "lease_lost")
        self.holder_confirms.append((job_id, body))
        if body.sha256 != self.jobs[job_id].sha256:
            return error(422, "hash_mismatch")
        self._end_lease(body.lease_id)
        answer = HolderConfirmResponse(confirmed_holder_count=1, raw_deleted_from_vps=False)
        return JSONResponse(answer.model_dump(mode="json"))

    async def _thumbnail(self, request: Request) -> Response:
        if not self._lease_is_alive(request.headers.get("lease-id", "")):
            return error(409, "lease_lost")
        self.thumbnails[request.path_params["job_id"]] = await request.body()
        return Response(status_code=204)

    def _chunk_endpoint(self, kind: str):
        async def receive_chunk(request: Request) -> Response:
            job_id = request.path_params["job_id"]
            lease_id = request.headers.get("lease-id", "")
            offset = int(request.headers.get("upload-offset", "-1"))
            if kind == "proxies":
                self.proxy_frame_rate_headers.append(request.headers.get(FRAME_RATE_HEADER))
            if not self._authorized(request):
                response = error(401, "unauthenticated")
            elif not self._lease_is_alive(lease_id):
                response = error(409, "lease_lost")
            else:
                response = self._accept_chunk(kind, job_id, lease_id, request, await request.body())
            self.upload_log.append((kind, response.status_code, offset))
            return response

        return receive_chunk

    def _accept_chunk(self, kind, job_id, lease_id, request, chunk) -> Response:
        accepted = receive_chunk(self.stored_upload(kind, job_id), request.headers, chunk)
        if isinstance(accepted, Response):  # the receiver refused the chunk
            return accepted
        if accepted.complete:
            self._end_lease(lease_id)  # completing chunk ends the lease
        if self.after_chunk_accepted is not None:
            self.after_chunk_accepted(kind, accepted.received_bytes)
        return JSONResponse(accepted.model_dump())

    async def _failed(self, request: Request) -> Response:
        body = FailedRequest.model_validate(await request.json())
        if not self._lease_is_alive(body.lease_id):
            return error(409, "lease_lost")
        self.failures.append((request.path_params["job_id"], body))
        self._end_lease(body.lease_id)
        return JSONResponse({"job_id": request.path_params["job_id"], "state": "failed"})

    async def _delivered(self, request: Request) -> Response:
        if self.delivered_calls_to_fail > 0:
            self.delivered_calls_to_fail -= 1
            return error(503, "upstream_error")
        if self.delivered_refusal is not None:
            return error(*self.delivered_refusal)
        job_id = request.path_params["job_id"]
        self.delivered.append((job_id, DeliveredRequest.model_validate(await request.json())))
        # The studio's ACK ends the pushing worker's process lease.
        for lease in list(self.leases.values()):
            if lease.job_id == job_id:
                self._end_lease(lease.lease_id)
        return JSONResponse({"job_id": job_id, "state": "done"})

    async def _outbox_ack(self, request: Request) -> Response:
        body = OutboxAckRequest.model_validate(await request.json())
        self.outbox_acks.append((request.path_params["job_id"], body))
        self._end_lease(body.lease_id)
        return JSONResponse({"job_id": request.path_params["job_id"], "state": "done"})

    async def _get_job(self, request: Request) -> Response:
        job = self.jobs.get(request.path_params["job_id"])
        if job is None:
            return error(404, "not_found")
        return JSONResponse(job.as_job().model_dump(mode="json"))

    def _file_endpoint(self, files: dict[str, bytes]):
        """Caddy-like static files: bearer required, Range supported."""

        async def serve(request: Request) -> Response:
            self.file_requests.append((request.url.path, dict(request.headers)))
            if not self._authorized(request):
                return Response(status_code=401)
            content = files.get(request.path_params["name"])
            if content is None:
                return Response(status_code=404)
            return range_response(content, request.headers.get("range"))

        return serve
