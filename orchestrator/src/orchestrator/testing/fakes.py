"""In-memory stand-ins for the four outside services, as httpx transports.

Each fake is an httpx.MockTransport handler: hand `httpx.Client(transport=
fake.transport())` to build_context() and the orchestrator talks to the
fake instead of the network. Each fake records what it was sent, so tests
can assert on it ("the bot replied once", "the key was tagged tag:worker").

    FakeWhatsAppCdn   serves encrypted media blobs by path
    FakeEvolutionApi  accepts sendText and remembers every message
    FakeTailscaleApi  OAuth token + auth-key minting, checked like the real spec
    FakeBajaApp       team names by car number
"""

import json
import re
import threading
from dataclasses import dataclass, field
from urllib.parse import parse_qs

import httpx

FAKE_CDN_BASE_URL = "http://fake-cdn.test"
FAKE_EVOLUTION_URL = "http://fake-evolution.test"
FAKE_TAILSCALE_API_BASE = "http://fake-tailscale.test"
FAKE_BAJA_APP_URL = "http://fake-baja-app.test"
FAKE_TAILSCALE_CLIENT_ID = "fake-client-id"
FAKE_TAILSCALE_CLIENT_SECRET = "fake-client-secret"


class FakeWhatsAppCdn:
    def __init__(self) -> None:
        self.blobs: dict[str, bytes] = {}
        self.requested_paths: list[str] = []
        # Set to make the next N requests fail with HTTP 500.
        self.failures_to_inject = 0

    def add_blob(self, path: str, blob: bytes) -> str:
        """Serve `blob` at `path` (e.g. "/v/abc.enc"); return its full URL."""
        self.blobs[path] = blob
        return FAKE_CDN_BASE_URL + path

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requested_paths.append(request.url.path)
        if self.failures_to_inject > 0:
            self.failures_to_inject -= 1
            return httpx.Response(500)
        blob = self.blobs.get(request.url.path)
        if blob is None:
            return httpx.Response(404)
        return httpx.Response(200, content=blob)

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)


@dataclass
class SentText:
    instance: str
    number: str
    text: str
    api_key: str | None


class FakeEvolutionApi:
    def __init__(self) -> None:
        self.sent: list[SentText] = []
        self._lock = threading.Lock()

    def handle(self, request: httpx.Request) -> httpx.Response:
        match = re.fullmatch(r"/message/sendText/([^/]+)", request.url.path)
        if request.method != "POST" or match is None:
            return httpx.Response(404)
        body = json.loads(request.content)
        with self._lock:
            self.sent.append(
                SentText(
                    match.group(1), body["number"], body["text"], request.headers.get("apikey")
                )
            )
        return httpx.Response(201, json={"key": {"id": "FAKE"}, "status": "PENDING"})

    def texts_to(self, number: str) -> list[str]:
        return [message.text for message in self.sent if message.number == number]

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)


@dataclass
class FakeTailscaleApi:
    """Implements the two calls of infra/tailnet/mint_auth_key.md and checks their shape."""

    issued_access_tokens: list[str] = field(default_factory=list)
    minted_keys: list[dict] = field(default_factory=list)
    # Status codes to answer key requests with before succeeding, e.g. [503].
    key_failures_to_inject: list[int] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    def handle(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v2/oauth/token":
            return self._token(request)
        if re.fullmatch(r"/api/v2/tailnet/[^/]+/keys", request.url.path):
            return self._create_key(request)
        return httpx.Response(404)

    def _token(self, request: httpx.Request) -> httpx.Response:
        form = {key: values[0] for key, values in parse_qs(request.content.decode()).items()}
        if (form.get("client_id"), form.get("client_secret")) != (
            FAKE_TAILSCALE_CLIENT_ID,
            FAKE_TAILSCALE_CLIENT_SECRET,
        ):
            return httpx.Response(401, json={"message": "invalid client"})
        token = f"tskey-api-fake-{len(self.issued_access_tokens)}"
        self.issued_access_tokens.append(token)
        return httpx.Response(200, json={"access_token": token, "expires_in": 3600})

    def _create_key(self, request: httpx.Request) -> httpx.Response:
        bearer = request.headers.get("Authorization", "").removeprefix("Bearer ")
        if bearer not in self.issued_access_tokens:
            return httpx.Response(401)
        if self.key_failures_to_inject:
            return httpx.Response(self.key_failures_to_inject.pop(0))
        body = json.loads(request.content)
        create = body["capabilities"]["devices"]["create"]
        if (create["reusable"], create["ephemeral"], create["preauthorized"]) != (
            False,
            False,
            True,
        ):
            self.problems.append(f"wrong key flags: {create}")
        if len(create["tags"]) != 1 or create["tags"][0] not in ("tag:worker", "tag:studio"):
            self.problems.append(f"wrong tags: {create['tags']}")
        if not re.fullmatch(r"[A-Za-z0-9 -]{1,50}", body["description"]):
            self.problems.append(f"bad description: {body['description']!r}")
        key_id = f"k{len(self.minted_keys):04d}CNTRL"
        minted = {"id": key_id, "key": f"tskey-auth-{key_id}-secret", **body}
        self.minted_keys.append(minted)
        return httpx.Response(200, json=minted)

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)


@dataclass
class FakeBajaApp:
    teams: dict[int, str] = field(default_factory=dict)
    requests: list[int] = field(default_factory=list)
    # When True every request answers 503 (the app is down).
    down: bool = False

    def handle(self, request: httpx.Request) -> httpx.Response:
        match = re.fullmatch(r"/api/teams/(\d+)", request.url.path)
        if match is None:
            return httpx.Response(404)
        if self.down:
            return httpx.Response(503)
        car_number = int(match.group(1))
        self.requests.append(car_number)
        if car_number not in self.teams:
            return httpx.Response(404, json={"detail": "unknown car"})
        return httpx.Response(
            200, json={"car_number": car_number, "team_name": self.teams[car_number]}
        )

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)
