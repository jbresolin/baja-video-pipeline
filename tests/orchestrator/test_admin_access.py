"""Who may call the admin routes, and the admin Users page.

Rules from the brief: admin routes need a user with role `admin` (a
reviewer gets 403); review routes accept a reviewer or an admin; the
static BVP_ADMIN_TOKEN keeps working for scripts.

The role test below does not list the admin routes by hand: it reads them
from the orchestrator's own route table, so a route added later is
checked automatically.
"""

import tempfile
from pathlib import Path

import pytest

from orchestrator.app import create_app
from orchestrator.testing.harness import SessionLogin, build_harness
from shared.testing.credentials import (
    OTHER_USERNAME,
    PLACEHOLDER_NEW_PASSWORD,
    PLACEHOLDER_OTHER_PASSWORD,
    PLACEHOLDER_SHORT_PASSWORD,
    REVIEWER_USERNAME,
    THIRD_USERNAME,
)

# Values for the {placeholders} in admin paths. They need not exist: the
# role check happens before the orchestrator looks anything up.
PATH_VALUES = {
    "{username}": "ana",
    "{phone}": "5511999999999",
    "{prova_id}": "1",
    "{car_number}": "42",
    "{token_id}": "0123456789abcdef",
    "{worker_id}": "0" * 32,
}


def admin_routes() -> list[tuple[str, str]]:
    """(method, path) of every route that needs an admin, from the app itself."""
    harness = build_harness_for_listing()
    document = create_app(harness.context, run_background_tasks=False).openapi()
    routes = []
    for path, operations in document["paths"].items():
        if path.startswith("/admin/") or path in ("/metrics", "/intake/test-upload"):
            for method in operations:
                for placeholder, value in PATH_VALUES.items():
                    path = path.replace(placeholder, value)
                routes.append((method.upper(), path))
    return sorted(routes)


def build_harness_for_listing():
    # Reading the route table needs a built app; any throw-away folder will do.
    return build_harness(Path(tempfile.mkdtemp()) / "data")


ADMIN_ROUTES = admin_routes()


def test_the_admin_route_list_is_not_empty():
    # A guard for the test below: if reading the routes broke, it would
    # silently test nothing.
    assert ("GET", "/admin/users") in ADMIN_ROUTES
    assert len(ADMIN_ROUTES) >= 20


@pytest.mark.parametrize(("method", "path"), ADMIN_ROUTES)
def test_a_reviewer_gets_403_and_nobody_signed_in_gets_401(harness, method, path):
    # A reviewer is signed in (so the orchestrator knows who it is) but is
    # not allowed: 403 "forbidden". Someone with no credentials: 401.
    as_reviewer = harness.client.request(method, path, json={}, auth=harness.reviewer_auth)
    assert as_reviewer.status_code == 403, as_reviewer.text
    assert as_reviewer.json()["error"]["code"] == "forbidden"
    anonymous = harness.client.request(method, path, json={})
    assert anonymous.status_code == 401


@pytest.mark.parametrize(
    "credentials", ["admin session", "admin token"], ids=["admin-session", "admin-token"]
)
def test_an_admin_session_and_the_admin_token_both_open_the_admin_routes(harness, credentials):
    kwargs = (
        {"auth": harness.admin_auth}
        if credentials == "admin session"
        else {"headers": harness.admin_headers}
    )
    assert harness.client.get("/admin/users", **kwargs).status_code == 200
    assert harness.client.get("/admin/settings", **kwargs).status_code == 200


def test_an_admin_may_also_use_the_review_routes(harness):
    # Review routes accept a reviewer OR an admin.
    assert harness.client.get("/review/queue", auth=harness.admin_auth).status_code == 200
    assert harness.client.get("/provas", auth=harness.admin_auth).status_code == 200


# --- Users page ---


def create_user(harness, username, password=PLACEHOLDER_OTHER_PASSWORD, role="reviewer"):
    return harness.client.post(
        "/admin/users",
        json={"username": username, "password": password, "role": role},
        auth=harness.admin_auth,
    )


def test_the_users_list_shows_names_and_roles_but_never_password_hashes(harness):
    users = harness.client.get("/admin/users", auth=harness.admin_auth).json()["users"]
    assert [(user["username"], user["role"]) for user in users] == [
        ("ana", "reviewer"),
        ("joao", "admin"),
    ]
    assert all("password" not in key for user in users for key in user)


def test_an_admin_adds_a_user_who_can_then_sign_in(harness):
    response = create_user(harness, "bruno", role="admin")
    assert response.status_code == 201
    assert response.json()["role"] == "admin"
    bruno = SessionLogin(OTHER_USERNAME, PLACEHOLDER_OTHER_PASSWORD)
    assert harness.client.get("/admin/users", auth=bruno).status_code == 200
    # The same name twice: 409 already_exists.
    again = create_user(harness, "bruno")
    assert (again.status_code, again.json()["error"]["code"]) == (409, "already_exists")


def test_short_passwords_and_odd_usernames_are_refused(harness):
    assert (
        create_user(harness, THIRD_USERNAME, password=PLACEHOLDER_SHORT_PASSWORD).status_code == 400
    )
    assert create_user(harness, "carla silva").status_code == 400


def test_disabling_a_user_ends_their_sessions_and_blocks_sign_in(harness):
    create_user(harness, "bruno")
    bruno = SessionLogin(OTHER_USERNAME, PLACEHOLDER_OTHER_PASSWORD)
    assert harness.client.get("/review/queue", auth=bruno).status_code == 200
    disabled = harness.client.patch(
        "/admin/users/bruno", json={"disabled": True}, auth=harness.admin_auth
    )
    assert disabled.json()["disabled"] is True
    # Bruno's open session is gone, and signing in again is refused.
    # (SessionLogin retries the sign-in once after a 401; it fails too.)
    assert harness.client.get("/review/queue", auth=bruno).status_code == 401
    # Enabling him again lets him back in.
    harness.client.patch("/admin/users/bruno", json={"disabled": False}, auth=harness.admin_auth)
    assert harness.client.get("/review/queue", auth=bruno).status_code == 200


def test_a_new_password_replaces_the_old_one_and_ends_old_sessions(harness):
    old_session = harness.reviewer_auth
    assert harness.client.get("/review/queue", auth=old_session).status_code == 200
    harness.client.patch(
        "/admin/users/ana", json={"password": PLACEHOLDER_NEW_PASSWORD}, auth=harness.admin_auth
    )
    # The old password no longer signs in (SessionLogin re-tries with it).
    assert harness.client.get("/review/queue", auth=old_session).status_code == 401
    new_session = SessionLogin(REVIEWER_USERNAME, PLACEHOLDER_NEW_PASSWORD)
    assert harness.client.get("/review/queue", auth=new_session).status_code == 200


def test_promoting_a_reviewer_makes_the_admin_pages_open_for_them(harness):
    assert harness.client.get("/admin/users", auth=harness.reviewer_auth).status_code == 403
    harness.client.patch("/admin/users/ana", json={"role": "admin"}, auth=harness.admin_auth)
    # The role is read on every request, so her current session is enough.
    assert harness.client.get("/admin/users", auth=harness.reviewer_auth).status_code == 200


def test_the_last_enabled_admin_cannot_be_disabled_or_demoted(harness):
    # Otherwise nobody could open the admin pages any more (only the
    # admin token from .env could fix it).
    for change in ({"disabled": True}, {"role": "reviewer"}):
        response = harness.client.patch("/admin/users/joao", json=change, auth=harness.admin_auth)
        assert (response.status_code, response.json()["error"]["code"]) == (409, "conflict")
    # With a second admin it is allowed.
    create_user(harness, "bruno", role="admin")
    response = harness.client.patch(
        "/admin/users/joao", json={"role": "reviewer"}, auth=harness.admin_auth
    )
    assert response.status_code == 200


def test_user_changes_are_audited_with_who_and_before_and_after(harness):
    create_user(harness, "bruno")
    harness.client.patch("/admin/users/bruno", json={"role": "admin"}, auth=harness.admin_auth)
    events = harness.client.get("/admin/audit?area=users", headers=harness.admin_headers).json()[
        "events"
    ]
    # Newest first: the role change, then the creation.
    assert [event["kind"] for event in events] == ["user_updated", "user_created"]
    change = events[0]
    assert change["actor"] == "admin:joao"
    assert change["details"]["before"]["role"] == "reviewer"
    assert change["details"]["after"]["role"] == "admin"
    # No password hash in the audit trail either.
    assert "password_hash" not in str(events)


def test_a_change_made_with_the_admin_token_is_audited_as_admin(harness):
    harness.client.post(
        "/admin/users",
        json={"username": "script", "password": PLACEHOLDER_OTHER_PASSWORD},
        headers=harness.admin_headers,
    )
    events = harness.client.get("/admin/audit", headers=harness.admin_headers).json()["events"]
    assert events[0]["actor"] == "admin"
