"""Tests for enrollment: admin tokens, POST /enroll, Tailscale minting, rate limit."""

from orchestrator.testing.harness import build_harness
from shared.models import WorkerRole


def create_token(harness, role="worker", hours=72) -> str:
    response = harness.client.post(
        "/admin/enrollment-tokens",
        json={"volunteer_name": "Bruno", "role": role, "valid_for_hours": hours},
        headers=harness.admin_headers,
    )
    assert response.status_code == 201
    return response.json()["token"]


def enroll_body(token: str) -> dict:
    return {
        "enrollment_token": token,
        "worker_name": "bruno-pc",
        "capabilities": {"has_nvenc": True, "video_encoder": "h264_nvenc"},
        "storage_cap_bytes": 200_000_000_000,
    }


def test_only_the_admin_can_create_enrollment_tokens(harness):
    response = harness.client.post(
        "/admin/enrollment-tokens", json={"volunteer_name": "x"}, auth=harness.reviewer_auth
    )
    assert response.status_code == 401


def test_enroll_exchanges_a_token_for_worker_id_bearer_and_tailscale_key(harness):
    token = create_token(harness, role="studio")
    response = harness.client.post("/enroll", json=enroll_body(token))
    assert response.status_code == 201
    body = response.json()
    assert body["role"] == "studio"
    assert body["tailscale_tags"] == ["tag:studio"]
    assert body["tailscale_auth_key"].startswith("tskey-auth-")
    assert (body["heartbeat_interval_s"], body["lease_ttl_s"]) == (15, 45)
    # The fake Tailscale API found nothing wrong with the minting request.
    assert harness.tailscale.problems == []
    # The bearer token works on worker endpoints.
    peers = harness.client.get(
        "/workers", headers={"Authorization": f"Bearer {body['bearer_token']}"}
    )
    assert peers.status_code == 200
    assert [peer["worker_id"] for peer in peers.json()["workers"]] == [body["worker_id"]]


def test_a_token_works_only_once(harness):
    token = create_token(harness)
    assert harness.client.post("/enroll", json=enroll_body(token)).status_code == 201
    second = harness.client.post("/enroll", json=enroll_body(token))
    assert second.status_code == 401
    assert second.json()["error"]["code"] == "enrollment_token_invalid"


def test_unknown_and_expired_tokens_are_refused_the_same_way(harness):
    unknown = harness.client.post("/enroll", json=enroll_body("made-up-token"))
    assert unknown.json()["error"]["code"] == "enrollment_token_invalid"
    token = create_token(harness, hours=1)
    harness.clock.advance(3601)  # one hour and one second later
    expired = harness.client.post("/enroll", json=enroll_body(token))
    assert expired.json()["error"]["code"] == "enrollment_token_invalid"


def test_a_tailscale_outage_answers_503_and_does_not_spend_the_token(harness):
    token = create_token(harness)
    # Three failures in a row exhaust the client's retries.
    harness.tailscale.key_failures_to_inject = [503, 503, 503]
    failed = harness.client.post("/enroll", json=enroll_body(token))
    assert failed.status_code == 503
    assert failed.json()["error"]["code"] == "upstream_error"
    # Tailscale is back: the very same token still works.
    assert harness.client.post("/enroll", json=enroll_body(token)).status_code == 201


def test_enroll_is_rate_limited_per_source_ip(tmp_path):
    harness = build_harness(tmp_path, enroll_rate_limit_requests=2)
    # Every request counts, even failed ones.
    for _ in range(2):
        assert harness.client.post("/enroll", json=enroll_body("x")).status_code == 401
    limited = harness.client.post("/enroll", json=enroll_body("x"))
    assert limited.status_code == 429
    assert limited.json()["error"]["code"] == "rate_limited"
    # Retry-After tells the worker how many seconds to wait (window 600 s).
    assert limited.headers["Retry-After"] == "600"
    # Malformed requests count too: the limit runs before body validation.
    assert harness.client.post("/enroll", json={}).status_code == 429


def test_rate_limit_uses_x_forwarded_for_from_caddy(tmp_path):
    # The in-memory client connects from 127.0.0.1, which the default
    # BVP_TRUSTED_PROXY_CIDRS trusts as Caddy, so X-Forwarded-For is used:
    # two different real clients get separate allowances.
    harness = build_harness(tmp_path, enroll_rate_limit_requests=1)
    first = {"X-Forwarded-For": "198.51.100.1"}
    second = {"X-Forwarded-For": "198.51.100.2"}
    assert harness.client.post("/enroll", json=enroll_body("x"), headers=first).status_code == 401
    assert harness.client.post("/enroll", json=enroll_body("x"), headers=second).status_code == 401
    assert harness.client.post("/enroll", json=enroll_body("x"), headers=first).status_code == 429


def test_the_harness_enrolls_every_role(harness):
    # A sanity check of the helper the other tests rely on.
    for role in WorkerRole:
        assert harness.enroll(role, f"{role}-pc").role == role
