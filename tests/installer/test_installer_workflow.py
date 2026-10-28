"""Static checks on .github/workflows/installer.yml.

The workflow itself runs on GitHub; these tests make sure the file keeps
the shape the PR promised (triggers, Windows runner, the steps that verify
and publish the installer) and parses as YAML.
"""

from pathlib import Path

import yaml

WORKFLOW_PATH = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "installer.yml"
WORKFLOW = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
# PyYAML reads the key `on:` as the boolean True (YAML 1.1 rule).
TRIGGERS = WORKFLOW.get("on", WORKFLOW.get(True))


def step_runs(job_name: str) -> str:
    """Every `run:` script and `uses:` action of one job, as one string."""
    steps = WORKFLOW["jobs"][job_name]["steps"]
    return "\n".join(str(step.get("run", "")) + str(step.get("uses", "")) for step in steps)


def test_it_runs_on_version_tags_and_by_hand():
    assert TRIGGERS["push"]["tags"] == ["v*"]
    assert "workflow_dispatch" in TRIGGERS


def test_the_installer_is_built_on_windows():
    assert WORKFLOW["jobs"]["windows"]["runs-on"] == "windows-latest"


def test_the_windows_job_verifies_builds_smoke_tests_and_publishes():
    runs = step_runs("windows")
    # In the order a build needs them.
    for expected in (
        "uv python install 3.12",
        "installer/fetch_vendor.py",
        "choco install innosetup",
        "installer/build.py --compile-installer",
        "--version",
        "--check",
        "smoke_install.ps1",
        "actions/upload-artifact",
        "gh release upload",
    ):
        assert expected in runs, expected
    positions = [runs.index(text) for text in ("fetch_vendor", "build.py", "--check", "gh release")]
    assert positions == sorted(positions)


def test_the_linux_job_runs_the_installer_tests_and_a_frozen_build():
    runs = step_runs("linux")
    assert "pytest tests/installer" in runs
    assert "installer/build.py --ffmpeg-from-path" in runs
    assert "BajaWorker --check" in runs
