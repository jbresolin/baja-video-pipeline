"""The localhost JSON API (worker.local_api) and the pt-BR status page on :8700.

The tray icon reads GET /api/status and presses POST /api/pause, /resume
and /stop; the status page's buttons call the same endpoints. Nothing here
needs a login (it is localhost only), so the tests also check what stops a
random web site in the volunteer's browser from pressing those buttons.
"""

import sys
import threading
import types

import httpx
import pytest

from shared.models import WorkerRole
from shared.testing.asgi_transport import SyncAsgiTransport
from worker.config import WorkerConfig, load_config, save_config
from worker.local_api import INSTALLER_BUILD_INFO_MODULE, worker_version
from worker.pause import PauseSwitch
from worker.setup_page import build_setup_app
from worker.status import StatusBoard

PORT = 8700
JSON = {"Content-Type": "application/json"}


@pytest.fixture
def api(tmp_path):
    """An enrolled worker's :8700 app. Returns (client, status, pause, config path, stops)."""
    config_path = tmp_path / "worker.json"
    save_config(
        config_path,
        WorkerConfig(
            worker_id="w-1",
            bearer_token="the-secret-token",
            role=WorkerRole.WORKER,
            worker_name="pc-da-ana",
            orchestrator_url="http://100.64.0.1:8080",
        ),
    )
    status = StatusBoard()
    pause = PauseSwitch(config_path)
    stops: list[str] = []  # each "Desligar" press adds one entry
    app = build_setup_app(
        config_path,
        status,
        threading.Event(),
        PORT,
        pause=pause,
        stop_service=lambda: stops.append("stop"),
    )
    client = httpx.Client(transport=SyncAsgiTransport(app), base_url=f"http://127.0.0.1:{PORT}")
    return client, status, pause, config_path, stops


def test_status_reports_facts_for_the_tray(api):
    client, status, _, _, _ = api
    # Set-up: what the heartbeat and a stage would have written.
    status.record_contact()
    status.set_storage(used_bytes=5 * 1024**3, cap_bytes=100 * 1024**3, held_raws=4)
    status.task_started("process", "job j1", job_id="j1", prova="Enduro", car_number=42)

    document = client.get("/api/status").json()

    assert document["enrolled"] is True
    assert document["paused"] is False
    assert document["connection_step_pending"] is False
    assert document["held_raws"] == 4
    assert document["storage"]["used_bytes"] == 5 * 1024**3
    assert document["tasks"] == [
        {"kind": "process", "job_id": "j1", "prova": "Enduro", "car_number": 42, "percent": None}
    ]
    assert document["last_contact_at"].endswith("Z")
    # The clip-archive numbers are not known yet: null, not 0.
    assert document["clips_held"] is None and document["clips_archived"] is None
    # Never the bearer token: the status is not a secret, the token is.
    assert "the-secret-token" not in client.get("/api/status").text


def test_pause_and_resume_change_the_switch_and_worker_json(api):
    client, _, pause, config_path, _ = api

    answer = client.post("/api/pause", content="{}", headers=JSON)
    assert answer.status_code == 200
    # The answer is the new status, so the tray can redraw at once.
    assert answer.json()["paused"] is True
    assert pause.is_paused
    # Remembered in worker.json: a reboot keeps it paused.
    assert load_config(config_path).paused is True

    client.post("/api/resume", content="{}", headers=JSON)
    assert not pause.is_paused
    assert load_config(config_path).paused is False


def test_stop_calls_the_service_stop_action(api):
    client, _, _, _, stops = api
    assert client.post("/api/stop", content="{}", headers=JSON).status_code == 200
    assert stops == ["stop"]


def test_a_plain_html_form_cannot_press_the_buttons(api):
    # A web page elsewhere could auto-submit a <form> to localhost:8700.
    # Forms cannot send Content-Type: application/json, so we refuse
    # anything else, and nothing changes.
    client, _, pause, _, stops = api
    form = {"Content-Type": "application/x-www-form-urlencoded"}
    assert client.post("/api/pause", content="x=1", headers=form).status_code == 403
    assert client.post("/api/stop", content="x=1", headers=form).status_code == 403
    assert not pause.is_paused and stops == []


def test_a_request_from_another_web_site_is_refused(api):
    # fetch() from evil.example would carry its Origin; only our own page's
    # origin is accepted.
    client, _, pause, _, _ = api
    headers = {**JSON, "Origin": "https://evil.example"}
    assert client.post("/api/pause", content="{}", headers=headers).status_code == 403
    assert not pause.is_paused
    # The status page itself (same origin) is fine.
    own = {**JSON, "Origin": f"http://localhost:{PORT}"}
    assert client.post("/api/pause", content="{}", headers=own).status_code == 200


def test_a_foreign_host_name_is_refused_even_for_the_status(api):
    # DNS rebinding: a name that points at 127.0.0.1 but is not "localhost".
    client, _, _, _, _ = api
    response = client.get("/api/status", headers={"Host": "attacker.example:8700"})
    assert response.status_code == 403


# --- the novice status page -------------------------------------------------


def test_the_status_page_is_portuguese_with_a_coloured_state_line(api):
    client, status, _, _, _ = api
    status.record_contact()
    status.set_storage(used_bytes=1024**3, cap_bytes=10 * 1024**3, held_raws=2)

    html = client.get("/").text

    assert "lang='pt-BR'" in html
    # Idle and connected: the blue state line, same words as the tray.
    assert "background:#386db4" in html
    assert "Conectado, aguardando vídeos" in html
    assert "1,0 GB de 10,0 GB" in html
    assert "Vídeos guardados" in html
    assert "Pausar" in html and "Desligar o worker" in html
    # Technical details are there but folded away.
    assert "<details><summary>Detalhes</summary>" in html
    assert "w-1" in html


def test_the_status_page_offers_resume_while_paused(api):
    client, _, pause, _, _ = api
    pause.pause()
    html = client.get("/").text
    assert "Retomar" in html and "act('pause')" not in html
    assert "Pausado" in html  # the state line


def test_the_clip_slot_appears_only_when_the_worker_reports_clips(api):
    client, status, _, _, _ = api
    assert "Clipes guardados" not in client.get("/").text
    status.set_clips(held=7, archived=5)
    assert "Clipes guardados" in client.get("/").text
    assert "arquivados pelo master: 5" in client.get("/").text


def test_the_page_explains_a_pending_connection_step(tmp_path):
    # Enrolled but still on the default orchestrator_url: the connection
    # step (item 6) is pending; the state line says so in yellow.
    config_path = tmp_path / "worker.json"
    save_config(config_path, WorkerConfig(worker_id="w", bearer_token="t", role=WorkerRole.WORKER))
    app = build_setup_app(config_path, StatusBoard(), threading.Event(), PORT)
    client = httpx.Client(transport=SyncAsgiTransport(app), base_url=f"http://127.0.0.1:{PORT}")
    html = client.get("/").text
    assert "Conexão não concluída" in html
    assert "background:#d9a400" in html


def test_an_installed_worker_reports_the_installers_version(monkeypatch):
    # Arrange: pretend this is a Windows build. installer/build.py writes a
    # small module holding the installer's version; here we put a stand-in
    # with "0.3.0" where the worker looks for it.
    stamped_build = types.ModuleType(INSTALLER_BUILD_INFO_MODULE)
    stamped_build.VERSION = "0.3.0"
    monkeypatch.setitem(sys.modules, INSTALLER_BUILD_INFO_MODULE, stamped_build)

    # Act: ask which version this worker is.
    version = worker_version()

    # Assert: the page shows the version the volunteer installed, not the
    # Python package's "0.1.0" that every installer showed until now.
    assert version == "0.3.0", "the :8700 page must show the installer's version"


def test_a_worker_run_from_source_still_reports_a_version(monkeypatch):
    # Arrange: no build module at all, as when a developer runs the worker
    # from the repository (None in sys.modules makes the import fail).
    monkeypatch.setitem(sys.modules, INSTALLER_BUILD_INFO_MODULE, None)

    # Act: ask which version this worker is.
    version = worker_version()

    # Assert: some version is still given (the Python package's), never a crash.
    assert version, "running from source must not break the status page"
