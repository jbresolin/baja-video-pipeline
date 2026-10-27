"""Tests that the UI acts as the reviewer: it forwards their Authorization header.

The review UI has no credentials of its own. Caddy checks the browser's
Basic credentials, then passes the request (Authorization header included)
to the UI, which must pass that header on to the orchestrator unchanged on
every call. The fake orchestrator answers 401 without it, like the real one.
"""

import base64

import pytest

from tests.review_ui.sample_data import REVIEWER_AUTHORIZATION

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
def test_every_orchestrator_call_carries_the_reviewers_authorization(ui, fake_orchestrator, url):
    response = ui.get(url)

    assert response.status_code == 200
    # At least one call reached the orchestrator, and every single one had
    # exactly the header the browser sent.
    assert fake_orchestrator.requests
    for request in fake_orchestrator.requests:
        assert request.headers["authorization"] == REVIEWER_AUTHORIZATION


def test_actions_carry_the_reviewers_authorization_too(ui, fake_orchestrator):
    ui.patch("/review/api/jobs/job-awaiting-1/review", json={"expected_version": 3})
    ui.post("/review/api/jobs/job-awaiting-1/regenerate-proxy", json={})

    assert len(fake_orchestrator.requests) == 2
    for request in fake_orchestrator.requests:
        assert request.headers["authorization"] == REVIEWER_AUTHORIZATION
    # The orchestrator recorded who reviewed: the user inside that header.
    assert fake_orchestrator.jobs["job-awaiting-1"].reviewed_by == "ana"


def test_x_reviewer_is_not_passed_to_the_orchestrator(ui, fake_orchestrator):
    # X-Reviewer is Caddy's convenience copy of the user name. The
    # orchestrator identifies reviewers by Authorization only, so the UI
    # does not forward the copy (nothing could ever trust it by mistake).
    ui.get("/review/", headers={"X-Reviewer": "ana"})

    assert "x-reviewer" not in fake_orchestrator.requests[0].headers


def test_without_credentials_the_page_asks_the_browser_to_sign_in(anonymous_ui):
    response = anonymous_ui.get("/review/")

    # 401 plus a Basic challenge makes the browser show its sign-in box
    # (useful without Caddy, e.g. in local development).
    assert response.status_code == 401
    assert response.headers["www-authenticate"].startswith("Basic ")
    assert "Sign-in needed" in response.text


def test_without_credentials_the_api_answers_json_401(anonymous_ui):
    response = anonymous_ui.patch(
        "/review/api/jobs/job-awaiting-1/review", json={"expected_version": 3}
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthenticated"


def test_wrong_credentials_are_rejected_by_the_orchestrator(anonymous_ui):
    wrong = "Basic " + base64.b64encode(b"ana:wrong-password").decode()

    response = anonymous_ui.get("/review/", headers={"Authorization": wrong})

    # The UI does not check passwords itself; the orchestrator's 401 decides.
    assert response.status_code == 401


def test_header_shows_the_reviewer_name_from_caddy(ui):
    html = ui.get("/review/", headers={"X-Reviewer": "ana-from-caddy"}).text

    assert 'class="who"' in html
    assert "ana-from-caddy" in html


def test_header_falls_back_to_the_user_name_in_the_basic_header(ui):
    # No X-Reviewer (no Caddy in front): the name is read from Authorization.
    html = ui.get("/review/").text

    assert 'title="Signed in as ana"' in html
