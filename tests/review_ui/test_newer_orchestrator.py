"""The review UI keeps working when the orchestrator is newer than it is.

Design §14: a client must never break because the server added a field.
Here the review UI talks to the REAL orchestrator (in memory), through
shared.testing.newer_server: every JSON answer gains an unknown field at
the top level and inside every nested object (the job's clip, its
versions and render inputs, the fleet's workers, the settings...). Every
page must still open, and a save must still go through.
"""

import httpx
import pytest

from orchestrator.testing.harness import REVIEWER_NAME, REVIEWER_PASSWORD
from review_ui.app import create_app
from review_ui.settings import ReviewUiSettings
from shared.testing.asgi_transport import SyncAsgiTransport
from shared.testing.newer_server import NewerServerTransport
from tests.orchestrator.test_render_inputs import (
    edit_review,
    enroll_fleet,
    ready_job,
    render_and_push,
)
from tests.review_ui.admin_browser import post_form


@pytest.fixture
def newer_orchestrator(harness):
    """The real orchestrator's answers, each with fields this UI does not know."""
    return NewerServerTransport(harness.client)


def browser_signed_in_through(answers: NewerServerTransport) -> httpx.Client:
    """A browser on a review UI whose calls to the orchestrator go through `answers`."""
    orchestrator = httpx.Client(transport=answers, base_url="http://orchestrator.test")
    app = create_app(
        ReviewUiSettings(orchestrator_url="http://orchestrator.test"),
        orchestrator_http_client=orchestrator,
    )
    browser = httpx.Client(transport=SyncAsgiTransport(app), base_url="https://review.test")
    signed_in = post_form(
        browser, "/review/login", username=REVIEWER_NAME, password=REVIEWER_PASSWORD
    )
    assert signed_in.status_code == 303, signed_in.text
    return browser


def test_every_review_page_opens_on_a_newer_orchestrator(harness, newer_orchestrator):
    # Setup: a delivered clip whose review was edited afterwards, so the
    # job carries a clip, its versions, render inputs and a comparison.
    master, studio = enroll_fleet(harness)
    job_id = ready_job(harness, master)
    render_and_push(harness, master, studio, job_id)
    edit_review(harness, job_id, cut_in_s=2.0)
    browser = browser_signed_in_through(newer_orchestrator)

    for page in (
        "/review/",
        "/review/?outdated=1",
        f"/review/jobs/{job_id}",
        f"/review/jobs/{job_id}/panel",
        "/review/clips",
        "/review/fleet",
    ):
        response = browser.get(page)
        assert response.status_code == 200, (page, response.text[:500])

    # The page still understood what it read: the outdated-clip banner is there.
    assert 'id="clip-outdated"' in browser.get(f"/review/jobs/{job_id}").text
    # The answers really carried the unknown fields.
    assert newer_orchestrator.widened_answers > 0


def test_a_save_goes_through_on_a_newer_orchestrator(harness, newer_orchestrator):
    master, _ = enroll_fleet(harness)
    job_id = ready_job(harness, master)
    browser = browser_signed_in_through(newer_orchestrator)
    version = harness.job(job_id)["version"]

    response = browser.patch(
        f"/review/api/jobs/{job_id}/review",
        json={"expected_version": version, "action": "save", "passagem": 5},
    )

    # The UI parsed the newer Job the orchestrator answered with.
    assert response.status_code == 200, response.text
    assert harness.job(job_id)["passagem"] == 5
