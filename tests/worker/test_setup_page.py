"""The localhost setup/status page (design §9): token, folder, cap; then status."""

import threading

import httpx
import pytest

from shared.models import WorkerRole
from shared.testing.asgi_transport import SyncAsgiTransport
from worker.config import GIGABYTE, WorkerConfig, load_config, save_config
from worker.setup_page import build_setup_app
from worker.status import StatusBoard

PORT = 8700


@pytest.fixture
def page(tmp_path):
    config_path = tmp_path / "worker.json"
    status = StatusBoard()
    submitted = threading.Event()
    app = build_setup_app(config_path, status, submitted, PORT)
    client = httpx.Client(transport=SyncAsgiTransport(app), base_url=f"http://127.0.0.1:{PORT}")
    return config_path, status, submitted, client


def test_first_visit_shows_the_three_field_form(page):
    _, _, _, client = page
    html = client.get("/").text
    for field_name in ("token", "folder", "cap_gb"):
        assert f"name='{field_name}'" in html


def test_submitting_the_form_writes_the_config_and_wakes_the_daemon(page, tmp_path):
    config_path, _, submitted, client = page
    folder = tmp_path / "baja-data"

    response = client.post(
        "/setup",
        content=f"token=abc123&folder={folder}&cap_gb=200",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )

    # The browser is sent back to the page (which will show progress).
    assert response.status_code == 303
    config = load_config(config_path)
    assert config.enrollment_token == "abc123"
    assert config.working_folder == folder
    assert config.storage_cap_bytes == 200 * GIGABYTE
    # The daemon is waiting on this event to start enrolling.
    assert submitted.is_set()


def test_incomplete_form_is_not_saved(page):
    config_path, status, submitted, client = page
    client.post("/setup", content="token=abc&folder=relative/path&cap_gb=10")
    assert not config_path.exists()
    assert not submitted.is_set()
    assert "fill in all three" in status.snapshot()[0]["setup"]


def test_requests_for_another_host_name_are_refused(page):
    # A malicious web page could resolve its own name to 127.0.0.1 ("DNS
    # rebinding") and post to us; its Host header would not be localhost.
    _, _, _, client = page
    response = client.get("/", headers={"Host": "evil.example:8700"})
    assert response.status_code == 403


def test_after_enrollment_the_page_shows_status_and_refuses_new_setup(page):
    config_path, status, _, client = page
    save_config(
        config_path,
        WorkerConfig(worker_id="w-1", bearer_token="t", role=WorkerRole.WORKER, worker_name="pc"),
    )
    status.set("last heartbeat", "2026-10-26 10:00:00 UTC")
    status.task_started("process", "job abc")

    html = client.get("/").text
    assert "w-1" in html and "2026-10-26 10:00:00 UTC" in html and "job abc" in html
    assert "name='token'" not in html
    assert client.post("/setup", content="token=x&folder=/x&cap_gb=1").status_code == 409


def test_values_are_html_escaped(page):
    config_path, status, _, client = page
    save_config(config_path, WorkerConfig(worker_id="w", bearer_token="t", role=WorkerRole.WORKER))
    status.set("orchestrator", "<script>alert(1)</script>")
    assert "<script>" not in client.get("/").text
