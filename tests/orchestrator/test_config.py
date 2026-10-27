"""Tests for orchestrator.config: reading BVP_* settings and failing fast.

The orchestrator must refuse to start when a required variable is missing,
and say *which* one, instead of crashing later on the first request.
"""

import os

import pytest

from orchestrator.config import ConfigError, load_settings

# The smallest complete set of required settings, by field name.
REQUIRED = {
    "admin_token": "a",
    "webhook_secret": "w",
    "public_base_url": "https://review.example.test",
    "tailnet_base_url": "http://100.64.0.1:8080",
    "evolution_api_key": "k",
    "baja_app_url": "http://baja.test",
    "tailscale_oauth_client_id": "id",
    "tailscale_oauth_client_secret": "secret",
}


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    # monkeypatch undoes every change after the test. Here we remove any
    # BVP_*/TAILSCALE_* variables from the developer's own shell, so the
    # tests see only what they set themselves.
    for name in list(os.environ):
        if name.startswith(("BVP_", "TAILSCALE_")):
            monkeypatch.delenv(name)


def test_defaults_match_docs_api_section_10():
    settings = load_settings(**REQUIRED)
    # Values straight from the table in docs/api.md §10.
    assert str(settings.data_dir) == "/srv/pipeline"
    assert settings.inbox_hold_bytes == 22_000_000_000
    assert settings.inbox_cap_bytes == 25_000_000_000
    assert settings.enroll_rate_limit_requests == 10
    assert settings.enroll_rate_limit_window_s == 600
    assert settings.tailscale_tailnet == "-"
    assert settings.whatsapp_media_base_url == "https://mmg.whatsapp.net"


def test_missing_required_variables_are_named_in_the_error():
    # Leave out two required settings and read the error message.
    incomplete = {k: v for k, v in REQUIRED.items() if k not in ("admin_token", "baja_app_url")}
    with pytest.raises(ConfigError) as caught:
        load_settings(**incomplete)
    message = str(caught.value)
    # The message names the *environment variables* a human must set.
    assert "BVP_ADMIN_TOKEN" in message
    assert "BVP_BAJA_APP_URL" in message


def test_settings_are_read_from_the_environment(monkeypatch):
    # Set every required value as a real environment variable, the way
    # docker compose does, and check they arrive.
    monkeypatch.setenv("BVP_ADMIN_TOKEN", "from-env")
    monkeypatch.setenv("BVP_WEBHOOK_SECRET", "w")
    monkeypatch.setenv("BVP_PUBLIC_BASE_URL", "https://x.test")
    monkeypatch.setenv("BVP_TAILNET_BASE_URL", "http://100.64.0.1:8080")
    monkeypatch.setenv("BVP_EVOLUTION_API_KEY", "k")
    monkeypatch.setenv("BVP_BAJA_APP_URL", "http://baja.test")
    monkeypatch.setenv("TAILSCALE_OAUTH_CLIENT_ID", "id")
    monkeypatch.setenv("TAILSCALE_OAUTH_CLIENT_SECRET", "secret")
    monkeypatch.setenv("BVP_INBOX_CAP_GB", "30")
    settings = load_settings()
    assert settings.admin_token.get_secret_value() == "from-env"
    assert settings.inbox_cap_bytes == 30_000_000_000
    assert settings.tailscale_oauth_client_id == "id"


def test_the_compose_file_name_for_the_evolution_key_is_accepted(monkeypatch):
    # infra/vps/docker-compose.yml passes BVP_EVOLUTION_INSTANCE_TOKEN
    # while docs/api.md says BVP_EVOLUTION_API_KEY. Both must work.
    monkeypatch.setenv("BVP_EVOLUTION_INSTANCE_TOKEN", "instance-token")
    without_key = {k: v for k, v in REQUIRED.items() if k != "evolution_api_key"}
    settings = load_settings(**without_key)
    assert settings.evolution_api_key.get_secret_value() == "instance-token"


def test_hold_above_cap_is_refused():
    # A soft limit above the hard limit makes no sense; refuse to start.
    with pytest.raises(ConfigError, match="BVP_INBOX_HOLD_GB"):
        load_settings(**REQUIRED, inbox_hold_gb=30, inbox_cap_gb=25)


def test_a_typo_in_trusted_proxy_cidrs_is_refused():
    with pytest.raises(ConfigError, match="BVP_TRUSTED_PROXY_CIDRS"):
        load_settings(**REQUIRED, trusted_proxy_cidrs="127.0.0.1/32,not-a-network")
