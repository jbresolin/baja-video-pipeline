"""Tests for OrchestratorClient edge cases and for the settings.

These use httpx.MockTransport: a tiny function plays the orchestrator and
returns whatever the test wants, including broken answers.
"""

import httpx
import pytest

from review_ui.app import create_app
from review_ui.orchestrator_client import (
    OrchestratorClient,
    OrchestratorError,
    OrchestratorUnavailableError,
)
from review_ui.settings import ReviewUiSettings
from shared.testing.asgi_transport import SyncAsgiTransport
from tests.review_ui.sample_data import REVIEWER_AUTHORIZATION, make_job


def client_answering(handler) -> OrchestratorClient:
    http_client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://orch")
    return OrchestratorClient(http_client, REVIEWER_AUTHORIZATION)


def test_job_ids_are_escaped_in_the_url_path():
    # Ids are opaque. A "/" inside one must stay inside the id, not create
    # a different path (which could reach another endpoint).
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.raw_path)
        return httpx.Response(200, json=make_job().model_dump(mode="json"))

    client_answering(handler).get_job("a/b")

    assert seen == [b"/jobs/a%2Fb"]


def test_error_body_is_read_into_code_message_and_details():
    def handler(request: httpx.Request) -> httpx.Response:
        body = {"error": {"code": "version_conflict", "message": "stale", "details": {"x": 1}}}
        return httpx.Response(409, json=body)

    with pytest.raises(OrchestratorError) as raised:
        client_answering(handler).get_job("j")

    assert raised.value.status_code == 409
    assert raised.value.code == "version_conflict"
    assert raised.value.details == {"x": 1}


def test_error_without_the_api_shape_still_becomes_an_orchestrator_error():
    # E.g. a proxy in between answering with an HTML page.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, text="<html>Bad gateway</html>")

    with pytest.raises(OrchestratorError) as raised:
        client_answering(handler).fleet()

    assert raised.value.status_code == 502
    assert raised.value.code == "upstream_error"


def test_answer_that_breaks_the_contract_is_reported_not_crashed_on():
    # A 200 whose body is not a Job (e.g. a mismatched orchestrator version).
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": "j"})

    with pytest.raises(OrchestratorUnavailableError, match="Job"):
        client_answering(handler).get_job("j")


def test_unreachable_orchestrator_shows_a_502_page():
    # Setup: a transport that fails like a refused connection.
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    orchestrator = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://orch")
    app = create_app(ReviewUiSettings(), orchestrator_http_client=orchestrator)
    ui = httpx.Client(transport=SyncAsgiTransport(app), base_url="http://testserver")

    page = ui.get("/review/", headers={"Authorization": REVIEWER_AUTHORIZATION})
    api = ui.get("/review/api/teams/42", headers={"Authorization": REVIEWER_AUTHORIZATION})

    # The page explains the problem; the page script gets the JSON shape.
    assert page.status_code == 502
    assert "Orchestrator unreachable" in page.text
    assert api.status_code == 502
    assert api.json()["error"]["code"] == "upstream_error"


def test_health_endpoint_needs_no_orchestrator():
    app = create_app(ReviewUiSettings(), orchestrator_http_client=httpx.Client())
    ui = httpx.Client(transport=SyncAsgiTransport(app), base_url="http://testserver")

    assert ui.get("/healthz").json() == {"status": "ok"}


def test_settings_come_from_bvp_environment_variables():
    settings = ReviewUiSettings.from_environment(
        {
            "BVP_ORCHESTRATOR_URL": "http://orchestrator:8000",
            "BVP_PUBLIC_BASE_URL": "https://baja.example.org/",
            "BVP_STUDIO_CLIP_ROOT": "D:\\clips",
        }
    )

    assert settings.orchestrator_url == "http://orchestrator:8000"
    # A trailing slash is removed so "/proxies/…" can be appended safely.
    assert settings.public_base_url == "https://baja.example.org"
    assert settings.port == 8001
    assert settings.studio_clip_root == "D:\\clips"
