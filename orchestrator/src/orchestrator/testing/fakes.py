"""In-memory stand-ins for the four outside services, as httpx transports.

Each fake is an httpx.MockTransport handler: hand `httpx.Client(transport=
fake.transport())` to build_context() and the orchestrator talks to the
fake instead of the network. Each fake records what it was sent, so tests
can assert on it ("the bot replied once", "the key was tagged tag:worker").

    FakeWhatsAppCdn   serves encrypted media blobs by path
    FakeEvolutionApi  accepts sendText / sendReaction and remembers each one
    FakeTailscaleApi  OAuth token + auth-key minting, checked like the real spec
    (the Baja results API's fake is in fake_results_api.py)
"""

import json
import re
import threading
from dataclasses import dataclass, field
from urllib.parse import parse_qs

import httpx

from shared.testing.credentials import PLACEHOLDER_TAILSCALE_CLIENT_SECRET

FAKE_CDN_BASE_URL = "http://fake-cdn.test"
FAKE_EVOLUTION_URL = "http://fake-evolution.test"
FAKE_TAILSCALE_API_BASE = "http://fake-tailscale.test"
FAKE_TAILSCALE_CLIENT_ID = "fake-client-id"
FAKE_TAILSCALE_CLIENT_SECRET = PLACEHOLDER_TAILSCALE_CLIENT_SECRET


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
    # Evolution's `quoted` ({key, message}) when the text replies to a message.
    quoted: dict | None = None


@dataclass
class SentReaction:
    instance: str
    # {"remoteJid", "fromMe", "id"} of the message reacted to.
    key: dict
    reaction: str
    api_key: str | None


class FakeEvolutionApi:
    """sendText and sendReaction, checked against Evolution 2.3.7's own schemas.

    The checks mirror src/validate/message.schema.ts at tag 2.3.7
    (textMessageSchema, quotedOptionsSchema, reactionMessageSchema); a
    request that would fail there is answered 400 and noted in `problems`.
    """

    def __init__(self) -> None:
        self.sent: list[SentText] = []
        self.reactions: list[SentReaction] = []
        self.problems: list[str] = []
        self._lock = threading.Lock()
        # What GET /instance/connectionState and /webhook/find answer (Settings page).
        self.connection_state = "open"
        self.webhook = {
            "enabled": True,
            "url": "http://bvp-orchestrator:8000/intake/evolution-webhook",
        }
        # True: every request fails as if Evolution were not running.
        self.down = False
        # An HTTP status (e.g. 500): sendText answers it, with Evolution's
        # error shape, and sends nothing.
        self.refuse_texts_with: int | None = None

    def handle(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("connection refused (fake Evolution is down)", request=request)
        if self.refuse_texts_with is not None and "/message/sendText/" in request.url.path:
            return httpx.Response(
                self.refuse_texts_with,
                json={"status": self.refuse_texts_with, "message": ["Connection Closed"]},
            )
        if request.method == "GET" and request.url.path.startswith("/instance/connectionState/"):
            instance = request.url.path.rsplit("/", 1)[-1]
            return httpx.Response(
                200, json={"instance": {"instanceName": instance, "state": self.connection_state}}
            )
        if request.method == "GET" and request.url.path.startswith("/webhook/find/"):
            return httpx.Response(200, json=self.webhook)
        match = re.fullmatch(r"/message/(sendText|sendReaction)/([^/]+)", request.url.path)
        if request.method != "POST" or match is None:
            return httpx.Response(404)
        endpoint, instance = match.groups()
        body = json.loads(request.content)
        api_key = request.headers.get("apikey")
        with self._lock:
            problem = _text_problem(body) if endpoint == "sendText" else _reaction_problem(body)
            if problem:
                self.problems.append(problem)
                return httpx.Response(400, json={"message": [problem]})
            if endpoint == "sendText":
                self.sent.append(
                    SentText(instance, body["number"], body["text"], api_key, body.get("quoted"))
                )
            else:
                self.reactions.append(
                    SentReaction(instance, body["key"], body["reaction"], api_key)
                )
        return httpx.Response(201, json={"key": {"id": "FAKE"}, "status": "PENDING"})

    def texts_to(self, number: str) -> list[str]:
        return [message.text for message in self.sent if message.number == number]

    def reactions_on(self, message_id: str) -> list[str]:
        """Every reaction sent on one message, in order (WhatsApp shows the last)."""
        return [r.reaction for r in self.reactions if r.key.get("id") == message_id]

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)


def _text_problem(body: dict) -> str | None:
    if not isinstance(body.get("number"), str) or not isinstance(body.get("text"), str):
        return "sendText needs string number and text"
    quoted = body.get("quoted")
    if quoted is not None:
        key = quoted.get("key") if isinstance(quoted, dict) else None
        if not isinstance(key, dict) or not key.get("id"):
            return "quoted.key.id is required"
        if not isinstance(quoted.get("message", {}), dict):
            return "quoted.message must be an object"
    return None


def _reaction_problem(body: dict) -> str | None:
    key = body.get("key")
    if not isinstance(key, dict):
        return "sendReaction needs a key"
    if not key.get("id") or not key.get("remoteJid") or not isinstance(key.get("fromMe"), bool):
        return "key.id, key.remoteJid and key.fromMe are required"
    if not isinstance(body.get("reaction"), str):
        return "reaction must be a string"
    return None


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
