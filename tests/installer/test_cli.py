"""BajaWorker.exe's command line (installer/baja_installer/cli.py).

Each test calls cli.main() with a list of arguments, exactly as Windows
would pass them to the exe, and replaces the expensive part (the worker
itself, the wait for Tailscale) with a small stand-in.
"""

import pytest

from baja_installer import cli
from worker.config import load_config


def test_version_prints_the_build_version(capsys):
    # From source there is no stamped build, so it says so.
    assert cli.main(["--version"]) == 0
    assert capsys.readouterr().out.strip() == "0.0.0+source"


def test_init_config_writes_the_bundled_ffmpeg_path(tmp_path):
    config_path = tmp_path / "worker.json"

    assert cli.main(["--init-config", str(config_path)]) == 0

    # From source the "install folder" is installer/, so ffmpeg is expected
    # in installer/ffmpeg/. In the real install: Program Files\BajaWorker\ffmpeg.
    assert load_config(config_path).ffmpeg_path.endswith("ffmpeg")


@pytest.fixture
def worker_calls(monkeypatch):
    """Replace the worker's main() with one that records its arguments."""
    calls = []

    def fake_worker_main(argv):
        calls.append(argv)
        return 0

    monkeypatch.setattr("worker.__main__.main", fake_worker_main)
    return calls


def test_without_a_command_it_runs_the_worker_with_the_config(tmp_path, worker_calls):
    config_path = tmp_path / "worker.json"

    assert cli.main(["--config", str(config_path), "--console"]) == 0

    # The worker gets the absolute config path and every unknown argument.
    assert worker_calls == [["--config", str(config_path.resolve()), "--console"]]


def test_service_mode_runs_the_worker_at_once(tmp_path, worker_calls):
    # NSSM starts "BajaWorker.exe --service". It used to wait for the
    # Tailscale address before even starting the worker, so the status page
    # never came up while the address was missing (LIVESTACK-41 item 1).
    # Now the worker starts straight away: its page comes up first and the
    # tailnet wait is a visible state inside the worker (worker.tailnet_wait).
    assert cli.main(["--service", "--config", str(tmp_path / "worker.json")]) == 0

    # "--service" is ours; the worker itself never sees it.
    assert worker_calls == [["--config", str((tmp_path / "worker.json").resolve())]]


def test_two_commands_at_once_are_refused():
    # argparse exits with code 2 for a usage error.
    with pytest.raises(SystemExit) as stopped:
        cli.main(["--version", "--check"])
    assert stopped.value.code == 2
