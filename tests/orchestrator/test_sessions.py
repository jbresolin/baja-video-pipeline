"""Tests for signing in: POST /auth/login, /auth/logout, GET /auth/me and GET /auth/media.

Background for a beginner: the review UI used to rely on Caddy's "Basic
auth" pop-up. Now the orchestrator keeps users in its database. Signing
in gives a random "session token"; the review UI keeps it in a cookie and
sends it back on every call as `Authorization: Bearer <token>`. A session
ends at logout, after 12 hours without any request, or when an admin
disables the user.

The `harness` fixture (tests/orchestrator/conftest.py) is a fresh
orchestrator whose users were imported from a reviewers.txt on its first
start: "ana" (reviewer) and "joao" (admin, via BVP_BOOTSTRAP_ADMIN).
"""

from orchestrator.testing.harness import (
    ADMIN_NAME,
    ADMIN_PASSWORD,
    REVIEWER_NAME,
    REVIEWER_PASSWORD,
    build_harness,
)

# ManualClock.advance() takes seconds.
HOUR = 3600


def login(harness, username, password, **kwargs):
    return harness.client.post(
        "/auth/login", json={"username": username, "password": password}, **kwargs
    )


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_the_right_password_gives_a_session_that_identifies_the_user(harness):
    # Sign in as the reviewer imported from reviewers.txt.
    response = login(harness, REVIEWER_NAME, REVIEWER_PASSWORD)
    assert response.status_code == 200
    body = response.json()
    # The answer names the user and their role, and says how long a quiet
    # session lives (12 h = 43200 s).
    assert body["user"] == {"username": "ana", "role": "reviewer"}
    assert body["idle_timeout_s"] == 43200
    # The token works: /auth/me answers with the same user...
    me = harness.client.get("/auth/me", headers=bearer(body["session_token"]))
    assert me.json() == {"username": "ana", "role": "reviewer"}
    # ...and so do the review routes.
    queue = harness.client.get("/review/queue", headers=bearer(body["session_token"]))
    assert queue.status_code == 200


def test_the_admin_is_the_user_named_in_bvp_bootstrap_admin(harness):
    body = login(harness, ADMIN_NAME, ADMIN_PASSWORD).json()
    assert body["user"]["role"] == "admin"


def test_wrong_password_and_unknown_user_get_the_same_401(harness):
    # The answer must not reveal whether the username exists.
    wrong_password = login(harness, REVIEWER_NAME, "not-her-password")
    unknown_user = login(harness, "nobody", "whatever")
    assert wrong_password.status_code == unknown_user.status_code == 401
    assert wrong_password.json()["error"] == unknown_user.json()["error"]


def test_the_session_token_is_stored_only_as_a_hash(harness):
    # A copy of the database must not contain usable session tokens.
    token = login(harness, REVIEWER_NAME, REVIEWER_PASSWORD).json()["session_token"]
    with harness.context.database.transaction() as connection:
        stored = [row[0] for row in connection.execute("SELECT token_sha256 FROM sessions")]
    assert token not in stored
    assert len(stored) == 1 and len(stored[0]) == 64


def test_logout_ends_the_session(harness):
    token = login(harness, REVIEWER_NAME, REVIEWER_PASSWORD).json()["session_token"]
    assert harness.client.post("/auth/logout", headers=bearer(token)).status_code == 204
    # The same token is now refused everywhere.
    assert harness.client.get("/auth/me", headers=bearer(token)).status_code == 401


def test_a_session_ends_after_12_hours_without_requests_but_activity_keeps_it(harness):
    token = login(harness, REVIEWER_NAME, REVIEWER_PASSWORD).json()["session_token"]
    # Use it every 11 hours: each request counts as activity, so the
    # session lives on well past 12 hours from the sign-in.
    for _ in range(3):
        harness.clock.advance(11 * HOUR)
        assert harness.client.get("/auth/me", headers=bearer(token)).status_code == 200
    # Then 12 quiet hours: the session is gone.
    harness.clock.advance(12 * HOUR)
    assert harness.client.get("/auth/me", headers=bearer(token)).status_code == 401


def test_the_media_check_reads_the_session_cookie(harness):
    # Caddy asks GET /auth/media before serving /proxies/* and /thumbs/*,
    # passing the browser's Cookie header on (forward_auth).
    token = login(harness, REVIEWER_NAME, REVIEWER_PASSWORD).json()["session_token"]
    allowed = harness.client.get("/auth/media", headers={"Cookie": f"other=1; bvp_session={token}"})
    assert allowed.status_code == 204
    # No cookie, or a made-up one: 401, which Caddy shows the browser.
    assert harness.client.get("/auth/media").status_code == 401
    forged = harness.client.get("/auth/media", headers={"Cookie": "bvp_session=made-up"})
    assert forged.status_code == 401


def test_the_media_check_does_not_write_the_database_on_every_range_request(harness):
    # The video player sends many Range requests; each passes /auth/media.
    # last_seen_at is refreshed at most once a minute, not on every one.
    token = login(harness, REVIEWER_NAME, REVIEWER_PASSWORD).json()["session_token"]

    def last_seen():
        with harness.context.database.transaction() as connection:
            return connection.execute("SELECT last_seen_at FROM sessions").fetchone()[0]

    signed_in_at = last_seen()
    harness.clock.advance(30)
    harness.client.get("/auth/media", headers={"Cookie": f"bvp_session={token}"})
    assert last_seen() == signed_in_at  # 30 s later: not written
    harness.clock.advance(31)
    harness.client.get("/auth/media", headers={"Cookie": f"bvp_session={token}"})
    assert last_seen() > signed_in_at  # over a minute: refreshed


def test_sign_in_attempts_are_rate_limited_per_source_address(tmp_path):
    # A harness with a small limit: 3 attempts per 600 s per address.
    harness = build_harness(tmp_path / "data", login_rate_limit_requests=3)
    for _ in range(3):
        login(harness, REVIEWER_NAME, "guess")
    # The 4th attempt is refused before the password is even looked at,
    # even with the right password, and says how long to wait.
    refused = login(harness, REVIEWER_NAME, REVIEWER_PASSWORD)
    assert refused.status_code == 429
    assert refused.json()["error"]["code"] == "rate_limited"
    assert int(refused.headers["Retry-After"]) > 0
    # After the window (600 s) the address may try again.
    harness.clock.advance(601)
    assert login(harness, REVIEWER_NAME, REVIEWER_PASSWORD).status_code == 200
