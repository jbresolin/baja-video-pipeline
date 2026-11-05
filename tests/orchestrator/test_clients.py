"""Tests for the outside-service clients: Tailscale minting, Evolution replies, Baja app teams.

Each client gets an httpx.Client wired to an in-memory fake, so we can see
exactly what it sends and make the "server" misbehave on purpose.
"""

import httpx
import pytest

from orchestrator.clients.evolution import EvolutionClient
from orchestrator.clients.tailscale import (
    FALLBACK_KEY_EXPIRY_SECONDS,
    TailscaleClient,
    TailscaleError,
)
from orchestrator.clock import ManualClock
from orchestrator.testing.fakes import (
    FAKE_EVOLUTION_URL,
    FAKE_TAILSCALE_API_BASE,
    FAKE_TAILSCALE_CLIENT_ID,
    FAKE_TAILSCALE_CLIENT_SECRET,
    FakeEvolutionApi,
    FakeTailscaleApi,
)
from shared.models import WorkerRole


def make_tailscale_client(fake: FakeTailscaleApi, clock=None, secret=FAKE_TAILSCALE_CLIENT_SECRET):
    return TailscaleClient(
        httpx.Client(transport=fake.transport()),
        FAKE_TAILSCALE_API_BASE,
        FAKE_TAILSCALE_CLIENT_ID,
        secret,
        "-",
        clock or ManualClock(),
        sleep=lambda seconds: None,  # no real waiting between retries
    )


def test_minted_key_follows_the_spec_and_is_tagged_by_role():
    fake = FakeTailscaleApi()
    client = make_tailscale_client(fake)
    worker_key = client.mint_auth_key(WorkerRole.WORKER, "baja worker enr-abc")
    studio_key = client.mint_auth_key(WorkerRole.STUDIO, "baja studio enr-def")
    # The fake checks reusable/ephemeral/preauthorized/tags/description and
    # writes down anything wrong in `problems`; an empty list means all good.
    assert fake.problems == []
    assert worker_key.tags == ["tag:worker"]
    assert studio_key.tags == ["tag:studio"]
    assert worker_key.key.startswith("tskey-auth-")
    # One-hour expiry for the unused key, as infra/tailnet/mint_auth_key.md says.
    assert fake.minted_keys[0]["expirySeconds"] == 3600


def test_the_access_token_is_reused_until_shortly_before_it_expires():
    fake = FakeTailscaleApi()
    clock = ManualClock()
    client = make_tailscale_client(fake, clock)
    client.mint_auth_key(WorkerRole.WORKER, "baja worker one")
    client.mint_auth_key(WorkerRole.WORKER, "baja worker two")
    # Two keys, but only one OAuth token request.
    assert len(fake.issued_access_tokens) == 1
    # 3600 s lifetime minus a 60 s margin: after 3541 s a new token is fetched.
    clock.advance(3541)
    client.mint_auth_key(WorkerRole.WORKER, "baja worker three")
    assert len(fake.issued_access_tokens) == 2


def test_a_rejected_one_hour_expiry_falls_back_to_one_day():
    fake = FakeTailscaleApi(key_failures_to_inject=[400])
    client = make_tailscale_client(fake)
    client.mint_auth_key(WorkerRole.WORKER, "baja worker x")
    assert fake.minted_keys[0]["expirySeconds"] == FALLBACK_KEY_EXPIRY_SECONDS


def test_outages_are_retried_then_reported():
    # Two 503s and then success: the retries hide a short outage.
    fake = FakeTailscaleApi(key_failures_to_inject=[503, 503])
    make_tailscale_client(fake).mint_auth_key(WorkerRole.WORKER, "baja worker x")
    assert len(fake.minted_keys) == 1
    # Three 503s in a row: give up with TailscaleError (→ 503 upstream_error).
    still_down = FakeTailscaleApi(key_failures_to_inject=[503, 503, 503])
    with pytest.raises(TailscaleError):
        make_tailscale_client(still_down).mint_auth_key(WorkerRole.WORKER, "baja worker x")


def test_wrong_oauth_credentials_are_a_clear_configuration_error():
    client = make_tailscale_client(FakeTailscaleApi(), secret="wrong")
    with pytest.raises(TailscaleError, match="TAILSCALE_OAUTH_CLIENT_ID"):
        client.mint_auth_key(WorkerRole.WORKER, "baja worker x")


def test_send_text_posts_the_number_and_text_with_the_api_key():
    fake = FakeEvolutionApi()
    client = EvolutionClient(
        httpx.Client(transport=fake.transport()), FAKE_EVOLUTION_URL, "the-key", "baja-bot"
    )
    assert client.send_text("5511999999999@s.whatsapp.net", "olá")
    # Evolution wants the bare phone number, not the "@s.whatsapp.net" jid.
    sent = fake.sent[0]
    assert (sent.instance, sent.number, sent.text, sent.api_key) == (
        "baja-bot",
        "5511999999999",
        "olá",
        "the-key",
    )


def test_a_failing_bot_reply_is_logged_not_raised():
    # Intake must never break because Evolution is down.
    down = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(500)))
    client = EvolutionClient(down, FAKE_EVOLUTION_URL, "k", "baja-bot")
    assert client.send_text("55@s.whatsapp.net", "x") is False
