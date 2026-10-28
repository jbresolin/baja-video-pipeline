"""`BajaWorker.exe --init-config <path>`: the installer's part of worker.json.

Run by the installer on every install and upgrade, before the service
starts. It sets only the keys the installer owns and keeps everything else
(an enrolled worker's identity, the volunteer's folder and cap):

* `ffmpeg_path`: the bundled ffmpeg in the install folder;
* `enroll_url`: the public site that routes POST /enroll, stamped into the
  build (build.py --enroll-url). A build without one leaves the key alone.

`orchestrator_url` is not known before enrollment; first_run.py takes it
from the /enroll answer (`tailnet_base_url`).
"""

from pathlib import Path

from worker.config import WorkerConfig, load_config, save_config


def write_installer_config(
    config_path: Path, *, ffmpeg_path: Path, enroll_url: str | None
) -> WorkerConfig:
    config = load_config(config_path)
    updates: dict[str, object] = {"ffmpeg_path": str(ffmpeg_path)}
    if enroll_url:
        updates["enroll_url"] = enroll_url
    updated = config.model_copy(update=updates)
    save_config(config_path, updated)
    return updated
