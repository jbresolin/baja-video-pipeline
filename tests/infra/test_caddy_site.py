"""The public Caddy site (infra/vps/conf.d/pipeline.caddy) and the orchestrator agree.

There is no Caddy in the test suite, so these tests do two things:

1. Read the site block as text and check the parts the rest of the system
   relies on: no password prompt from Caddy any more; /proxies/* and
   /thumbs/* and /clip-downloads/* go through forward_auth to the
   orchestrator's /auth/media;
   the bare domain redirects to /review/.
2. Call the orchestrator's /auth/media the way Caddy's forward_auth does
   (the browser's own headers, cookie included, plus X-Forwarded-Method
   and X-Forwarded-Uri) and check it answers what Caddy expects: 2xx to
   serve the file, 401 to refuse.

So if someone renames the endpoint, the cookie or the Caddy route, a test
here fails instead of the video player going black at the event.
"""

import re
from pathlib import Path

import pytest

from orchestrator.domain.sessions import SESSION_COOKIE_NAME as ORCHESTRATOR_COOKIE
from orchestrator.testing.harness import REVIEWER_NAME, REVIEWER_PASSWORD, build_harness
from review_ui.session import SESSION_COOKIE_NAME as REVIEW_UI_COOKIE
from tools.generate_openapi import OPENAPI_PATH

SITE = Path(__file__).resolve().parents[2] / "infra" / "vps" / "conf.d" / "pipeline.caddy"


def site_text() -> str:
    """The site block without comments (so prose cannot satisfy or break a check)."""
    lines = [line.split("#", 1)[0].rstrip() for line in SITE.read_text().splitlines()]
    return "\n".join(line for line in lines if line.strip())


def block(text: str, opening: str) -> str:
    """The text between `opening {` and its matching `}` (Caddyfile blocks nest)."""
    start = text.index(opening + " {")
    depth = 0
    for index in range(start, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    raise AssertionError(f"unclosed block {opening!r}")


def test_caddy_no_longer_asks_for_passwords_or_reads_reviewers_txt():
    text = site_text()
    assert "basic_auth" not in text
    assert "reviewers.txt" not in text
    # Nothing copies a Caddy-authenticated user name any more either.
    assert "X-Reviewer" not in text


def test_proxies_and_thumbs_ask_the_orchestrator_before_serving():
    text = site_text()
    files = block(text, "(bvp_pipeline_files)")
    # forward_auth comes first in the snippet, to the orchestrator's alias.
    assert re.search(r"forward_auth bvp-orchestrator:8000 \{\s*uri /auth/media\s*\}", files)
    assert files.index("forward_auth") < files.index("file_server")
    for route in ("handle /proxies/*", "handle /thumbs/*"):
        assert "import bvp_pipeline_files" in block(text, route)


def test_clip_downloads_ask_the_orchestrator_before_serving_the_outbox():
    # LIVESTACK-38: the review UI's Download button for a clip still in the
    # outbox. Its own block (it serves outbox/ under another path), but the
    # same sign-in check as proxies, before any file is served.
    downloads = block(site_text(), "handle_path /clip-downloads/*")
    assert re.search(r"forward_auth bvp-orchestrator:8000 \{\s*uri /auth/media\s*\}", downloads)
    assert downloads.index("forward_auth") < downloads.index("file_server")
    assert "/outbox" in downloads
    # Half-written uploads are never handed out.
    assert "hide .* *.part *.part.json" in downloads


def test_the_forward_auth_target_is_in_the_contract_and_reads_the_same_cookie():
    contract = OPENAPI_PATH.read_text(encoding="utf-8")
    assert "  /auth/media:" in contract
    # The review UI sets the cookie; the orchestrator reads it; same name.
    assert REVIEW_UI_COOKIE == ORCHESTRATOR_COOKIE == "bvp_session"


def test_the_bare_domain_redirects_to_the_review_ui():
    # Why "*": in a Caddyfile a first argument starting with "/" is a
    # request matcher. "redir /review/ 302" therefore meant "for requests
    # to /review/, redirect to '302'", which never matched "/", and Caddy
    # answered the bare domain with an empty 200. That was the old bug.
    root = block(site_text(), "handle /")
    assert "redir * /review/ 302" in root


def test_every_redir_names_its_matcher_explicitly():
    # Guards against the same bug anywhere else in the file.
    for line in site_text().splitlines():
        words = line.split()
        if words and words[0] == "redir":
            assert words[1] == "*" or words[1].startswith("@"), line


def test_the_review_ui_route_stays_and_enroll_stays_post_only():
    text = site_text()
    assert "reverse_proxy bvp-review-ui:8001" in block(text, "handle /review*")
    enroll = block(text, "@bvp_enroll")
    assert "path /enroll" in enroll and "method POST" in enroll


# --- The orchestrator side of forward_auth ---


@pytest.fixture
def harness(tmp_path):
    built = build_harness(tmp_path / "data")
    yield built
    built.context.database.close()


def as_forward_auth(
    cookie: str | None, uri: str = "/proxies/0123456789abcdef.mp4"
) -> dict[str, str]:
    """The headers Caddy's forward_auth sends: the browser's own, plus these two."""
    headers = {
        "X-Forwarded-Method": "GET",
        "X-Forwarded-Uri": uri,
        "Range": "bytes=0-",  # the video player's
        "X-Forwarded-For": "203.0.113.9",
    }
    if cookie is not None:
        headers["Cookie"] = cookie
    return headers


def test_a_signed_in_browser_is_let_through(harness):
    login = harness.client.post(
        "/auth/login", json={"username": REVIEWER_NAME, "password": REVIEWER_PASSWORD}
    )
    token = login.json()["session_token"]

    response = harness.client.get(
        "/auth/media", headers=as_forward_auth(f"theme=dark; bvp_session={token}")
    )

    # Any 2xx tells Caddy to serve the file.
    assert response.status_code == 204


def test_no_cookie_a_forged_one_or_an_ended_session_is_refused(harness):
    login = harness.client.post(
        "/auth/login", json={"username": REVIEWER_NAME, "password": REVIEWER_PASSWORD}
    )
    token = login.json()["session_token"]
    harness.client.post("/auth/logout", headers={"Authorization": f"Bearer {token}"})

    for cookie in (None, "bvp_session=forged", f"bvp_session={token}"):
        response = harness.client.get("/auth/media", headers=as_forward_auth(cookie))
        # Caddy passes this 401 back to the browser instead of the file.
        assert response.status_code == 401, cookie


def test_a_clip_download_without_a_session_is_refused(harness):
    # What Caddy asks before serving /clip-downloads/<job>.mp4: someone with
    # the link but no sign-in (no cookie, or a made-up one) gets 401, so
    # finished clips never leak through a shared URL.
    clip = "/clip-downloads/0123456789abcdef0123456789abcdef.mp4"
    for cookie in (None, "bvp_session=forged"):
        response = harness.client.get("/auth/media", headers=as_forward_auth(cookie, clip))
        assert response.status_code == 401, cookie

    # A signed-in reviewer with the same link is let through.
    login = harness.client.post(
        "/auth/login", json={"username": REVIEWER_NAME, "password": REVIEWER_PASSWORD}
    )
    cookie = f"bvp_session={login.json()['session_token']}"
    response = harness.client.get("/auth/media", headers=as_forward_auth(cookie, clip))
    assert response.status_code == 204
