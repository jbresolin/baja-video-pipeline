"""`BajaWorker.exe --check` (installer/baja_installer/check.py).

CI runs --check on the real frozen exe. Here the same function runs on
Linux against a fake install folder whose "ffmpeg" and "ffprobe" are tiny
shell scripts, so we can make them succeed or fail at will.
"""

import os
import stat
import sys

import pytest

from baja_installer.check import run_check

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="fake tools are sh scripts")


def write_fake_tool(folder, name, script_body):
    """Create an executable shell script folder/name."""
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_text("#!/bin/sh\n" + script_body + "\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def fake_install(tmp_path, ffmpeg_body=None, ffprobe_body=None):
    """An install folder laid out like the real one: <install>/ffmpeg/ffmpeg."""
    install = tmp_path / "BajaWorker"
    tools = install / "ffmpeg"
    write_fake_tool(tools, "ffmpeg", ffmpeg_body or 'echo "ffmpeg version 9.0 fake"')
    write_fake_tool(tools, "ffprobe", ffprobe_body or 'echo "ffprobe version 9.0 fake"')
    return install


def test_a_complete_build_passes(tmp_path):
    # Set up: tools that answer like real ffmpeg, and modules that import.
    install = fake_install(tmp_path)
    output = []

    exit_code = run_check(install, ["json", "worker.config"], say=output.append)

    # Exit code 0 is what CI checks. The fake ffmpeg lists no encoders, so
    # the worker would fall back to libx264 (fine without a GPU).
    assert exit_code == 0
    assert "modules: 2/2 import" in output
    assert "video encoder: libx264" in output
    assert output[-1] == "check passed"


def test_a_module_that_does_not_import_fails_the_check(tmp_path):
    # A module PyInstaller left out looks exactly like this inside the exe.
    install = fake_install(tmp_path)
    output = []

    exit_code = run_check(install, ["json", "no_such_module_xyz"], say=output.append)

    assert exit_code == 1
    assert any("no_such_module_xyz" in line and "PROBLEM" in line for line in output)


def test_a_missing_ffmpeg_fails_the_check(tmp_path):
    install = fake_install(tmp_path)
    os.remove(install / "ffmpeg" / "ffmpeg")
    output = []

    assert run_check(install, [], say=output.append) == 1
    assert any("did not run" in line for line in output)


def test_an_ffmpeg_that_exits_with_an_error_fails_the_check(tmp_path):
    # e.g. a DLL missing next to ffmpeg.exe: it starts, prints nothing, fails.
    install = fake_install(tmp_path, ffmpeg_body="exit 1")
    output = []

    assert run_check(install, [], say=output.append) == 1
    assert any("exited 1" in line for line in output)


def test_a_program_that_is_not_ffprobe_fails_the_check(tmp_path):
    # Copying the wrong file into place must not pass silently.
    install = fake_install(tmp_path, ffprobe_body='echo "something else"')
    output = []

    assert run_check(install, [], say=output.append) == 1
