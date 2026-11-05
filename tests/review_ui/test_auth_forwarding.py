"""Tests that the UI acts as the signed-in user: their session goes along on every call.

The review UI has no credentials of its own. The browser signs in on
/review/login; the orchestrator answers with a session token, which the UI
keeps in the browser's bvp_session cookie (httpOnly, Secure, SameSite=Lax).
On every page the UI sends that token to the orchestrator as
`Authorization: Bearer <token>`. The fake orchestrator answers 401 without
it, like the real one, and the UI then sends the browser to the login page.
"""

import pytest

from tests.review_ui.sample_data import REVIEWER_AUTHORIZATION, REVIEWER_SESSION_TOKEN

# One URL per kind of page or action, so every orchestrator call is covered.
PAGES = [
    "/review/",
    "/review/partials/queue",
    "/review/jobs/job-awaiting-1",
    "/review/jobs/job-awaiting-1/panel",
    "/review/clips",
    "/review/fleet",
    "/review/partials/fleet",
    "/review/api/teams/42",
]


@pytest.mark.parametrize("url", PAGES)
def test_every_orchestrator_call_carries_the_users_session(ui, fake_orchestrator, url):
    response = ui.get(url)

    assert response.status_code == 200
    # At least one call reached the orchestrator, and every single one had
    # the session from the browser's cookie as a bearer token.
    assert fake_orchestrator.requests
    for request in fake_orchestrator.requests:
        assert request.headers["authorization"] == REVIEWER_AUTHORIZATION


def test_actions_carry_the_users_session_too(ui, fake_orchestrator):
    ui.patch("/review/api/jobs/job-awaiting-1/review", json={"expected_version": 3})
    ui.post("/review/api/jobs/job-awaiting-1/regenerate-proxy", json={})

    assert len(fake_orchestrator.requests) == 2
    for request in fake_orchestrator.requests:
        assert request.headers["authorization"] == REVIEWER_AUTHORIZATION
    # The orchestrator recorded who reviewed: the user of that session.
    assert fake_orchestrator.jobs["job-awaiting-1"].reviewed_by == "ana"


def test_without_a_session_a_page_sends_the_browser_to_the_login_page(anonymous_ui):
    response = anonymous_ui.get("/review/jobs/job-awaiting-1?x=1")

    # 303 "see other": the browser opens the login page, which remembers
    # where to come back to.
    assert response.status_code == 303
    assert response.headers["location"] == "/review/login?next=/review/jobs/job-awaiting-1%3Fx%3D1"


def test_without_a_session_the_api_answers_json_401(anonymous_ui):
    response = anonymous_ui.patch(
        "/review/api/jobs/job-awaiting-1/review", json={"expected_version": 3}
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthenticated"


def test_an_ended_session_also_goes_to_login_and_the_stale_cookie_is_cleared(anonymous_ui):
    # A cookie from a session the orchestrator no longer knows (logged out
    # elsewhere, 12 h idle): the orchestrator's 401 decides, not the UI.
    anonymous_ui.cookies.set("bvp_session", "an-old-token")

    response = anonymous_ui.get("/review/")

    assert response.status_code == 303
    assert response.headers["location"].startswith("/review/login")
    assert 'bvp_session=""' in response.headers["set-cookie"]


def test_the_header_shows_the_signed_in_user_and_a_logout_button(ui, fake_orchestrator):
    html = ui.get("/review/").text

    # The name comes from GET /auth/me (the orchestrator), never from a header.
    assert fake_orchestrator.requests_to("GET", "/auth/me")
    assert 'title="Conectado como ana (Revisor)"' in html
    assert 'action="/review/logout"' in html
    # Ana is a reviewer: no Admin link for her.
    assert 'href="/review/admin/"' not in html


def test_x_reviewer_is_not_passed_to_the_orchestrator(ui, fake_orchestrator):
    # Caddy used to add X-Reviewer. Nothing must ever trust such a header,
    # so the UI does not pass it on.
    ui.get("/review/", headers={"X-Reviewer": "mallory"})

    assert all("x-reviewer" not in request.headers for request in fake_orchestrator.requests)


def test_the_session_cookie_is_not_given_to_the_orchestrator_as_a_cookie(ui, fake_orchestrator):
    # The token travels as a bearer header; the UI does not forward cookies.
    ui.get("/review/")

    assert all("cookie" not in request.headers for request in fake_orchestrator.requests)
    assert REVIEWER_SESSION_TOKEN in fake_orchestrator.requests[0].headers["authorization"]
