"""Config file and first-run enrollment (design §8, §9)."""

import threading

import httpx

from shared.models import WorkerCapabilities, WorkerRole
from tests.worker.fake_orchestrator import (
    BASE_URL,
    GOOD_ENROLLMENT_TOKEN,
    TAILNET_URL,
    FakeOrchestrator,
)
from tests.worker.fakes import RoutingTransport
from worker.api_client import OrchestratorClient
from worker.config import WorkerConfig, load_config, save_config
from worker.enrollment import TAILSCALE_AUTH_KEY_FILE_NAME, wait_until_enrolled
from worker.status import StatusBoard

CAPABILITIES = WorkerCapabilities(has_nvenc=False, video_encoder="libx264")


def fake_network(tmp_path, role=WorkerRole.WORKER):
    orchestrator = FakeOrchestrator(tmp_path / "vps", role=role)
    network = RoutingTransport()
    network.add_app(BASE_URL, orchestrator.app)
    return orchestrator, network


def enroll_now(config_path, network, shutdown=None):
    """Run the enrollment loop once, the way __main__ does."""
    return wait_until_enrolled(
        config_path,
        api_for=lambda config: OrchestratorClient(httpx.Client(transport=network), BASE_URL),
        capabilities_for=lambda config: CAPABILITIES,
        status=StatusBoard(),
        setup_submitted=threading.Event(),
        shutdown=shutdown or threading.Event(),
        retry_seconds=0,
    )


def test_missing_config_means_defaults(tmp_path):
    config = load_config(tmp_path / "worker.json")
    assert config.is_enrolled is False
    assert config.resolved_working_folder(tmp_path / "worker.json") == tmp_path


def test_config_round_trips_and_leaves_no_temporary_file(tmp_path):
    path = tmp_path / "worker.json"
    save_config(path, WorkerConfig(storage_cap_bytes=123, ffmpeg_path="C:/ff/ffmpeg.exe"))
    assert load_config(path).storage_cap_bytes == 123
    assert [p.name for p in tmp_path.iterdir()] == ["worker.json"]


def test_ffprobe_is_found_next_to_ffmpeg():
    assert (
        WorkerConfig(ffmpeg_path="C:/ff/ffmpeg.exe").resolved_ffprobe_path() == "C:/ff/ffprobe.exe"
    )
    assert WorkerConfig(ffmpeg_path="ffmpeg").resolved_ffprobe_path() == "ffprobe"


def test_enrollment_saves_identity_forgets_token_and_writes_tailscale_key(tmp_path):
    # Setup: the setup page saved a token, a cap and the orchestrator URL.
    orchestrator, network = fake_network(tmp_path, role=WorkerRole.MASTER)
    config_path = tmp_path / "home" / "worker.json"
    save_config(
        config_path,
        WorkerConfig(
            orchestrator_url=BASE_URL,
            enrollment_token=GOOD_ENROLLMENT_TOKEN,
            storage_cap_bytes=50 * 1024**3,
        ),
    )

    enrolled = enroll_now(config_path, network)

    # The answer's identity is used straight away and saved for next time.
    saved = load_config(config_path)
    for config in (enrolled, saved):
        assert config.is_enrolled
        assert config.worker_id == "worker-1"
        assert config.role == WorkerRole.MASTER
        # The one-time token is used up: it must not linger in the file.
        assert config.enrollment_token is None
        # Caddy's tailnet listener from the answer: the second origin that
        # may see our bearer token (design §17).
        assert config.tailnet_base_url == TAILNET_URL
    # The installer picks up the Tailscale key from this file.
    key_file = config_path.parent / TAILSCALE_AUTH_KEY_FILE_NAME
    assert key_file.read_text().strip() == "tskey-auth-test"
    # The orchestrator was told the cap and the machine's capabilities.
    [request] = orchestrator.enroll_requests
    assert request.storage_cap_bytes == 50 * 1024**3
    assert request.capabilities.video_encoder == "libx264"


def test_refused_token_is_cleared_so_the_setup_page_asks_again(tmp_path):
    orchestrator, network = fake_network(tmp_path)
    config_path = tmp_path / "worker.json"
    save_config(config_path, WorkerConfig(orchestrator_url=BASE_URL, enrollment_token="wrong"))
    shutdown = threading.Event()

    # The loop would wait for a new token forever; stop it once the bad
    # token has been tried by setting shutdown from the "setup page" wait.
    original_load = load_config

    def stop_when_token_cleared():
        if original_load(config_path).enrollment_token is None:
            shutdown.set()

    timer = threading.Timer(0.5, stop_when_token_cleared)
    timer.start()
    result = enroll_now(config_path, network, shutdown)
    timer.join()

    assert result is None  # never enrolled
    assert load_config(config_path).enrollment_token is None
    assert not (tmp_path / TAILSCALE_AUTH_KEY_FILE_NAME).exists()


def test_already_enrolled_config_skips_enrollment(tmp_path):
    orchestrator, network = fake_network(tmp_path)
    config_path = tmp_path / "worker.json"
    save_config(
        config_path,
        WorkerConfig(worker_id="w", bearer_token="t", role=WorkerRole.WORKER),
    )
    assert enroll_now(config_path, network).worker_id == "w"
    assert orchestrator.enroll_requests == []


def test_config_enrolled_before_tailnet_base_url_existed_still_loads(tmp_path):
    # A worker.json written by an older version has no "tailnet_base_url"
    # key. It must still load as enrolled; the token then goes only to
    # orchestrator_url (worker.token_origins skips the missing one).
    config_path = tmp_path / "worker.json"
    config_path.write_text(
        '{"orchestrator_url": "http://100.64.0.1:8080", "worker_id": "w",'
        ' "bearer_token": "t", "role": "worker"}'
    )
    config = load_config(config_path)
    assert config.is_enrolled
    assert config.tailnet_base_url is None
