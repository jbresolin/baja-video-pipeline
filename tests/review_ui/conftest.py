"""Shared setup for the review UI tests.

pytest loads this file automatically, and every function marked
@pytest.fixture can be requested by a test just by naming it as a
parameter. The two main fixtures are:

* `fake_orchestrator`: the in-memory orchestrator (fake_orchestrator.py),
  preloaded with an awaiting-review job, a failed job and a delivered job.
* `ui`: a test "browser" (an httpx.Client) for the real review UI app,
  wired to that fake and sending the reviewer's Basic credentials, the way
  a browser does after Caddy asked for them.

Both hops (test → UI, UI → fake orchestrator) are real HTTP requests
handled in memory by SyncAsgiTransport: no network, no ports.
"""

import httpx
import pytest

from review_ui.app import create_app
from review_ui.settings import ReviewUiSettings
from shared.testing.asgi_transport import SyncAsgiTransport
from tests.review_ui.fake_orchestrator import FakeOrchestrator
from tests.review_ui.sample_data import (
    REVIEWER_AUTHORIZATION,
    delivered_job,
    failed_job,
    make_job,
)


@pytest.fixture
def fake_orchestrator() -> FakeOrchestrator:
    fake = FakeOrchestrator()
    fake.add_job(make_job("job-awaiting-1"))
    fake.add_job(failed_job("job-failed-1"))
    fake.add_job(delivered_job("job-done-1"))
    return fake


@pytest.fixture
def settings() -> ReviewUiSettings:
    # Empty public base URL: media links stay relative ("/proxies/…").
    return ReviewUiSettings(orchestrator_url="http://orch", public_base_url="")


def build_ui(fake: FakeOrchestrator, settings: ReviewUiSettings, headers: dict) -> httpx.Client:
    app = create_app(settings, orchestrator_http_client=fake.http_client())
    return httpx.Client(
        transport=SyncAsgiTransport(app), base_url="http://testserver", headers=headers
    )


@pytest.fixture
def ui(fake_orchestrator, settings) -> httpx.Client:
    return build_ui(fake_orchestrator, settings, {"Authorization": REVIEWER_AUTHORIZATION})


@pytest.fixture
def anonymous_ui(fake_orchestrator, settings) -> httpx.Client:
    """A browser that has not signed in (no Authorization header)."""
    return build_ui(fake_orchestrator, settings, {})
