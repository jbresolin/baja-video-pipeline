"""A test "browser" for the admin pages, wired to the REAL orchestrator (in memory).

The other review UI tests use a small fake orchestrator. The admin pages
are tested against the real one (orchestrator.testing.harness), so a test
that, say, blocks a contact on the page can then check that the
orchestrator really refuses that contact's next video.

    harness → the orchestrator with fakes for WhatsApp, Evolution, Tailscale
    browser → httpx client for the review UI, signed in through /review/login
"""

import httpx

from orchestrator.testing.harness import OrchestratorHarness
from review_ui.app import create_app
from review_ui.settings import ReviewUiSettings
from shared.testing.asgi_transport import SyncAsgiTransport

FORM_HEADERS = {"Content-Type": "application/x-www-form-urlencoded"}


def browser_for(harness: OrchestratorHarness) -> httpx.Client:
    """A browser (cookie jar included) for a review UI that talks to `harness`."""
    app = create_app(
        ReviewUiSettings(orchestrator_url="http://orchestrator.test"),
        orchestrator_http_client=harness.client,
    )
    # https:// because the session cookie is Secure (httpx, like a browser,
    # only sends it over HTTPS).
    return httpx.Client(transport=SyncAsgiTransport(app), base_url="https://review.test")


def signed_in_browser(harness: OrchestratorHarness, username: str, password: str) -> httpx.Client:
    browser = browser_for(harness)
    response = post_form(browser, "/review/login", username=username, password=password)
    assert response.status_code == 303, response.text
    return browser


def post_form(browser: httpx.Client, path: str, **fields) -> httpx.Response:
    """Submit a plain HTML form (urlencoded), like a browser does."""
    return browser.post(path, data=fields, headers=FORM_HEADERS)
