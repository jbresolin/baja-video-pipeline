"""A complete orchestrator wired to fakes, for tests and local experiments.

    harness = build_harness(tmp_path)
    worker = harness.enroll(WorkerRole.MASTER, "joao-desktop")
    harness.client.post(f"/workers/{worker.worker_id}/heartbeat", headers=worker.headers, ...)

Nothing touches the network: requests go to the app in memory
(SyncAsgiTransport) and the app's outgoing calls go to the fakes. Time is a
ManualClock, and background tasks do not run by themselves; call
orchestrator.background.sweepers.* to run one pass.
"""

from dataclasses import dataclass, field
from pathlib import Path

import bcrypt
import httpx

from orchestrator.app import build_context, create_app
from orchestrator.clock import ManualClock
from orchestrator.config import load_settings
from orchestrator.context import OrchestratorContext
from orchestrator.testing.fake_results_api import FAKE_RESULTS_API_URL, FakeResultsApi
from orchestrator.testing.fakes import (
    FAKE_CDN_BASE_URL,
    FAKE_EVOLUTION_URL,
    FAKE_TAILSCALE_API_BASE,
    FAKE_TAILSCALE_CLIENT_ID,
    FAKE_TAILSCALE_CLIENT_SECRET,
    FakeEvolutionApi,
    FakeTailscaleApi,
    FakeWhatsAppCdn,
)
from shared.models import WorkerRole
from shared.protocol import WEBHOOK_SECRET_HEADER
from shared.testing.asgi_transport import SyncAsgiTransport

ADMIN_TOKEN = "test-admin-token"
WEBHOOK_SECRET = "test-webhook-secret"
REVIEWER_NAME = "ana"
REVIEWER_PASSWORD = "senha-da-ana"
# Made admin on the first-start import (BVP_BOOTSTRAP_ADMIN).
ADMIN_NAME = "joao"
ADMIN_PASSWORD = "senha-do-joao"
PUBLIC_BASE_URL = "https://review.example.test"
# Dev mode is refused with a public URL (config.LOCAL_HOSTNAMES), so a
# dev-mode harness defaults to this one.
DEV_PUBLIC_BASE_URL = "http://127.0.0.1:8000"
TAILNET_BASE_URL = "http://100.64.0.1:8080"
# Plenty of room: workers in tests accept new raws unless a test says otherwise.
ROOMY_FREE_BYTES = 500_000_000_000


def write_reviewers_file(path: Path, passwords: dict[str, str]) -> Path:
    """Write a reviewers file in the Caddy format, with cheap bcrypt hashes (cost 4)."""
    lines = ["# test reviewers"]
    for username, password in passwords.items():
        password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=4)).decode()
        lines.append(f"{username} {password_hash}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


class SessionLogin(httpx.Auth):
    """httpx auth that signs in with POST /auth/login and sends the session as a bearer.

    It signs in on first use, and again after a 401 (e.g. a test moved the
    clock past the 12 h idle timeout), so tests can keep using one object.
    """

    requires_response_body = True

    def __init__(self, username: str, password: str) -> None:
        self.username = username
        self.password = password
        self.session_token: str | None = None

    def _login_request(self, request: httpx.Request) -> httpx.Request:
        return httpx.Request(
            "POST",
            request.url.copy_with(path="/auth/login", query=None),
            json={"username": self.username, "password": self.password},
        )

    def auth_flow(self, request: httpx.Request):
        for attempt in range(2):
            if self.session_token is None or attempt == 1:
                login = yield self._login_request(request)
                self.session_token = (
                    login.json()["session_token"] if login.status_code == 200 else None
                )
            if self.session_token is not None:
                request.headers["Authorization"] = f"Bearer {self.session_token}"
            response = yield request
            if response.status_code != 401 or self.session_token is None:
                return


@dataclass(frozen=True)
class EnrolledWorker:
    worker_id: str
    bearer_token: str
    role: WorkerRole

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.bearer_token}"}


@dataclass
class OrchestratorHarness:
    context: OrchestratorContext
    client: httpx.Client
    clock: ManualClock
    cdn: FakeWhatsAppCdn
    evolution: FakeEvolutionApi
    tailscale: FakeTailscaleApi
    results_api: FakeResultsApi

    # The static admin token (scripts), and two signed-in users.
    admin_headers = {"Authorization": f"Bearer {ADMIN_TOKEN}"}
    webhook_headers = {WEBHOOK_SECRET_HEADER: WEBHOOK_SECRET}
    reviewer_auth: SessionLogin = field(
        default_factory=lambda: SessionLogin(REVIEWER_NAME, REVIEWER_PASSWORD)
    )
    admin_auth: SessionLogin = field(
        default_factory=lambda: SessionLogin(ADMIN_NAME, ADMIN_PASSWORD)
    )

    def enroll(
        self,
        role: WorkerRole = WorkerRole.WORKER,
        name: str = "volunteer-pc",
        *,
        heartbeat: bool = True,
        tailnet_address: str | None = None,
    ) -> EnrolledWorker:
        """Issue a token, exchange it at /enroll and (by default) send a first heartbeat."""
        created = self.client.post(
            "/admin/enrollment-tokens",
            json={"volunteer_name": name, "role": role},
            headers=self.admin_headers,
        )
        created.raise_for_status()
        enrolled = self.client.post(
            "/enroll",
            json={
                "enrollment_token": created.json()["token"],
                "worker_name": name,
                "capabilities": {"has_nvenc": False, "video_encoder": "libx264"},
                "storage_cap_bytes": ROOMY_FREE_BYTES,
            },
        )
        enrolled.raise_for_status()
        body = enrolled.json()
        worker = EnrolledWorker(body["worker_id"], body["bearer_token"], WorkerRole(body["role"]))
        if heartbeat:
            self.heartbeat(worker, tailnet_address=tailnet_address)
        return worker

    def heartbeat(self, worker: EnrolledWorker, **fields) -> dict:
        body = {"free_bytes": ROOMY_FREE_BYTES, "storage_cap_bytes": ROOMY_FREE_BYTES, **fields}
        body = {key: value for key, value in body.items() if value is not None}
        response = self.client.post(
            f"/workers/{worker.worker_id}/heartbeat", json=body, headers=worker.headers
        )
        response.raise_for_status()
        return response.json()

    def add_team(self, car_number: int, team_name: str, short_name: str | None = None) -> None:
        """Put a car in the teams table (as an admin would; no results API sync)."""
        response = self.client.put(
            f"/admin/teams/{car_number}",
            json={"team_name": team_name, "short_name": short_name},
            headers=self.admin_headers,
        )
        response.raise_for_status()

    def job(self, job_id: str) -> dict:
        response = self.client.get(f"/jobs/{job_id}", auth=self.reviewer_auth)
        response.raise_for_status()
        return response.json()


def build_harness(data_dir: Path, **setting_overrides) -> OrchestratorHarness:
    data_dir.mkdir(parents=True, exist_ok=True)
    # Imported into the users table on the first start (domain/first_start.py).
    reviewers_file = write_reviewers_file(
        data_dir / "reviewers.txt",
        {REVIEWER_NAME: REVIEWER_PASSWORD, ADMIN_NAME: ADMIN_PASSWORD},
    )
    settings = load_settings(
        **{
            "data_dir": data_dir,
            "reviewers_file": reviewers_file,
            "bootstrap_admin": ADMIN_NAME,
            # Cheap bcrypt, and no sign-in limit getting in the way of many tests.
            "password_hash_rounds": 4,
            "login_rate_limit_requests": 10_000,
            "admin_token": ADMIN_TOKEN,
            "webhook_secret": WEBHOOK_SECRET,
            "public_base_url": (
                DEV_PUBLIC_BASE_URL if setting_overrides.get("dev_mode") else PUBLIC_BASE_URL
            ),
            "tailnet_base_url": TAILNET_BASE_URL,
            "whatsapp_media_base_url": FAKE_CDN_BASE_URL,
            "evolution_url": FAKE_EVOLUTION_URL,
            "evolution_api_key": "fake-evolution-key",
            "results_api_url": FAKE_RESULTS_API_URL,
            "tailscale_oauth_client_id": FAKE_TAILSCALE_CLIENT_ID,
            "tailscale_oauth_client_secret": FAKE_TAILSCALE_CLIENT_SECRET,
            "tailscale_api_base": FAKE_TAILSCALE_API_BASE,
            **setting_overrides,
        }
    )
    clock = ManualClock()
    cdn, evolution = FakeWhatsAppCdn(), FakeEvolutionApi()
    tailscale, results_api = FakeTailscaleApi(), FakeResultsApi()
    context = build_context(
        settings,
        clock=clock,
        media_http_client=httpx.Client(transport=cdn.transport()),
        evolution_http_client=httpx.Client(transport=evolution.transport()),
        tailscale_http_client=httpx.Client(transport=tailscale.transport()),
        results_api_http_client=httpx.Client(transport=results_api.transport()),
    )
    app = create_app(context, run_background_tasks=False)
    client = httpx.Client(transport=SyncAsgiTransport(app), base_url="http://orchestrator.test")
    return OrchestratorHarness(context, client, clock, cdn, evolution, tailscale, results_api)
