"""The display name field on the worker's token form (LIVESTACK-42 item 7).

Background for a beginner: before a PC joins the fleet, the volunteer opens
http://127.0.0.1:8700 and pastes the enrollment token. That form also asks
for the PC's name, prefilled with the host name Windows gave the machine
(often meaningless, like "DESKTOP-8F3K2LQ"). Whatever the volunteer leaves
there is saved as `worker_name` in worker.json and sent to the orchestrator
at enrollment, next to the real host name. Every page of the review UI then
shows that name.
"""

import platform
import threading

import httpx
import pytest

from shared.testing.asgi_transport import SyncAsgiTransport
from tests.worker.fake_orchestrator import BASE_URL, GOOD_ENROLLMENT_TOKEN
from tests.worker.test_enrollment import enroll_now, fake_network
from worker.config import WorkerConfig, load_config, save_config
from worker.display_name import display_name_change, display_name_input
from worker.setup_page import build_setup_app
from worker.status import StatusBoard

PORT = 8700
FORM = {"Content-Type": "application/x-www-form-urlencoded"}


@pytest.fixture
def page(tmp_path):
    """The setup page app on an in-memory connection, and its config path."""
    config_path = tmp_path / "worker.json"
    app = build_setup_app(config_path, StatusBoard(), threading.Event(), PORT)
    client = httpx.Client(transport=SyncAsgiTransport(app), base_url=f"http://127.0.0.1:{PORT}")
    return config_path, client


def test_the_form_asks_for_the_name_prefilled_with_the_host_name(page):
    config_path, client = page
    # The installer wrote nothing about the name, so it is the host name.
    save_config(config_path, WorkerConfig(worker_name="DESKTOP-8F3K2LQ"))

    html = client.get("/").text

    assert "Nome desta máquina" in html
    assert "name='worker_name'" in html
    assert "value='DESKTOP-8F3K2LQ'" in html


def test_the_typed_name_is_saved_with_the_token(page, tmp_path):
    config_path, client = page
    folder = tmp_path / "dados"

    response = client.post(
        "/setup",
        data={
            "token": "abc123",
            "worker_name": "  Notebook   da Maria ",
            "folder": str(folder),
            "cap_gb": "50",
        },
        headers=FORM,
    )

    # Saved, with runs of spaces squeezed to one.
    assert response.status_code == 303
    config = load_config(config_path)
    assert config.worker_name == "Notebook da Maria"
    assert config.enrollment_token == "abc123"


def test_a_blank_name_keeps_the_host_name():
    # A form sent without a name (or only spaces) changes nothing.
    assert display_name_change({"worker_name": "   "}) == {}
    assert display_name_change({}) == {}


def test_the_prefilled_name_is_html_escaped():
    # A host name is text from the machine: it must never become HTML.
    html = display_name_input(WorkerConfig(worker_name="pc'><script>x</script>"))

    assert "<script>" not in html
    assert "&#x27;&gt;&lt;script&gt;" in html


def test_enrollment_sends_the_display_name_and_the_real_host_name(tmp_path):
    # The volunteer named the PC; the fake orchestrator records what /enroll got.
    orchestrator, network = fake_network(tmp_path)
    config_path = tmp_path / "home" / "worker.json"
    save_config(
        config_path,
        WorkerConfig(
            orchestrator_url=BASE_URL,
            enrollment_token=GOOD_ENROLLMENT_TOKEN,
            worker_name="Notebook da Maria",
        ),
    )

    enroll_now(config_path, network)

    [request] = orchestrator.enroll_requests
    assert request.worker_name == "Notebook da Maria"
    # The host name goes separately, so the admin pages can show both.
    assert request.hostname == (platform.node() or None)
