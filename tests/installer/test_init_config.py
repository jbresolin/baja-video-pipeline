"""`BajaWorker.exe --init-config` (installer/baja_installer/init_config.py).

The installer runs it on every install and upgrade. It must set the
installer's two keys and never lose what enrollment or the volunteer put
into worker.json.
"""

from pathlib import Path

from baja_installer.init_config import write_installer_config
from shared.models import WorkerRole
from worker.config import WorkerConfig, load_config, save_config

FFMPEG = Path("C:/Program Files/BajaWorker/ffmpeg/ffmpeg.exe")


def test_a_fresh_install_writes_ffmpeg_and_the_enroll_url(tmp_path):
    # No worker.json yet: this is a brand-new install.
    config_path = tmp_path / "worker.json"

    write_installer_config(config_path, ffmpeg_path=FFMPEG, enroll_url="https://enroll.example")

    # The file now exists and the worker will read these two values.
    config = load_config(config_path)
    assert config.ffmpeg_path == str(FFMPEG)
    assert config.enroll_url == "https://enroll.example"
    # Not enrolled yet: the setup page will ask for the token.
    assert config.is_enrolled is False


def test_an_upgrade_keeps_the_identity_folder_and_cap(tmp_path):
    # Set up: a worker that enrolled and chose its own folder and cap.
    config_path = tmp_path / "worker.json"
    enrolled = WorkerConfig(
        worker_id="abc",
        bearer_token="secret",
        role=WorkerRole.WORKER,
        working_folder=Path("D:/Baja"),
        storage_cap_bytes=123,
        tailnet_address="100.64.0.7",
        orchestrator_url="http://100.64.0.1:8080",
    )
    save_config(config_path, enrolled)

    write_installer_config(config_path, ffmpeg_path=FFMPEG, enroll_url="https://enroll.example")

    # Only ffmpeg_path and enroll_url may differ; everything else is as before.
    after = load_config(config_path)
    assert after.model_copy(update={"ffmpeg_path": "ffmpeg", "enroll_url": None}) == enrolled


def test_a_build_without_an_enroll_url_leaves_the_key_alone(tmp_path):
    # A test build has no enroll URL stamped in; an existing value stays.
    config_path = tmp_path / "worker.json"
    save_config(config_path, WorkerConfig(enroll_url="https://set.by.hand"))

    write_installer_config(config_path, ffmpeg_path=FFMPEG, enroll_url=None)

    assert load_config(config_path).enroll_url == "https://set.by.hand"
