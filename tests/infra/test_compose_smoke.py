"""Tests for infra/vps/compose-smoke.sh and its helper infra/vps/smoke_checks.py.

The smoke test runs on the VPS after `docker compose up` and prints a
PASS/FAIL table. We cannot run Docker, Caddy or Evolution here, so:

* the first group of tests calls the helper's decision functions directly
  with text copied from what the real commands print;
* the last group runs the real shell script with fake `docker`, `curl` and
  `findmnt` programs placed first on PATH (the real `stat` looks at a
  temporary folder), and checks the table and the exit code.
"""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import bcrypt
import pytest

VPS_FOLDER = Path(__file__).resolve().parents[2] / "infra" / "vps"


def load_helper():
    """smoke_checks.py is a script, not a package: load it from its file."""
    spec = importlib.util.spec_from_file_location("smoke_checks", VPS_FOLDER / "smoke_checks.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


smoke = load_helper()


def statuses(lines: list[str]) -> list[str]:
    """Just the PASS/FAIL column of the helper's result lines."""
    return [line.split("\t")[0] for line in lines]


# --- Containers ---------------------------------------------------------------


def compose_line(service: str, state: str = "running", health: str = "healthy") -> str:
    """One line of `docker compose ps --format json` (compose >= 2.21)."""
    return json.dumps({"Service": service, "State": state, "Health": health})


def test_all_expected_containers_running_and_healthy_pass():
    # Caddy has no healthcheck, so compose reports Health as "".
    output = "\n".join(
        compose_line(service, health="" if service == "caddy" else "healthy")
        for service in smoke.EXPECTED_SERVICES
    )
    lines = smoke.check_containers(output, smoke.EXPECTED_SERVICES)
    assert statuses(lines) == ["PASS"] * len(smoke.EXPECTED_SERVICES)


def test_unhealthy_exited_and_missing_containers_each_fail_with_a_reason():
    output = "\n".join(
        [
            compose_line("evolution", health="unhealthy"),
            compose_line("postgres", state="exited", health=""),
            compose_line("redis"),
        ]
    )
    lines = smoke.check_containers(output, ["evolution", "postgres", "redis", "orchestrator"])
    # One line per expected service, in order: unhealthy, exited, fine, missing.
    assert statuses(lines) == ["FAIL", "FAIL", "PASS", "FAIL"]
    assert "unhealthy" in lines[0]
    assert "exited" in lines[1]
    assert "not created" in lines[3]


def test_older_compose_json_array_output_is_understood_too():
    # Compose before 2.21 printed one JSON array instead of one object per line.
    output = json.dumps([{"Service": "redis", "State": "running", "Health": "healthy"}])
    assert statuses(smoke.check_containers(output, ["redis"])) == ["PASS"]


# --- HTTP answers -------------------------------------------------------------


def test_http_check_needs_the_status_and_the_json_field():
    good = smoke.check_http("health", "200", '{"status": "ok"}', 200, "status=ok")
    wrong_body = smoke.check_http("health", "200", '{"status": "starting"}', 200, "status=ok")
    wrong_code = smoke.check_http("health", "502", "Bad Gateway", 200, "status=ok")
    # curl prints 000 when it never got an answer (DNS, firewall, nothing listening).
    no_connection = smoke.check_http("health", "000", "", 200, None)
    assert statuses(good + wrong_body + wrong_code + no_connection) == [
        "PASS",
        "FAIL",
        "FAIL",
        "FAIL",
    ]
    assert "no connection" in no_connection[0]


def test_evolution_must_be_open():
    open_body = json.dumps({"instance": {"instanceName": "baja", "state": "open"}})
    connecting = json.dumps({"instance": {"instanceName": "baja", "state": "connecting"}})
    assert statuses(smoke.check_evolution_state("200", open_body)) == ["PASS"]
    # "connecting" means the session is not up: the bot would not receive videos.
    failed = smoke.check_evolution_state("200", connecting)
    assert statuses(failed) == ["FAIL"] and "connecting" in failed[0]
    # A wrong API key gives 401.
    assert statuses(smoke.check_evolution_state("401", "")) == ["FAIL"]


def test_metrics_check_counts_online_workers():
    body = (
        '# TYPE bvp_jobs gauge\nbvp_jobs{state="received"} 2\n'
        'bvp_workers_online{role="master"} 1\nbvp_workers_online{role="worker"} 3\n'
    )
    lines = smoke.check_metrics("200", body)
    assert statuses(lines) == ["PASS"] and "4 worker(s) online" in lines[0]
    # A wrong admin token is a 401.
    assert statuses(smoke.check_metrics("401", "")) == ["FAIL"]


# --- Files on disk ------------------------------------------------------------


def a_bcrypt_hash() -> str:
    # Cost 4 keeps the test fast; the format is what matters.
    return bcrypt.hashpw(b"secret", bcrypt.gensalt(rounds=4)).decode()


def test_reviewers_file_rules(tmp_path):
    good = tmp_path / "good.txt"
    good.write_text(f"# team\nana {a_bcrypt_hash()}\nbruno {a_bcrypt_hash()}\n")
    htpasswd = tmp_path / "htpasswd.txt"
    htpasswd.write_text(f"ana:{a_bcrypt_hash()}\n")
    only_comments = tmp_path / "empty.txt"
    only_comments.write_text("# nobody yet\n")

    assert statuses(smoke.check_reviewers_file(good)) == ["PASS"]
    # htpasswd's "user:hash" is the classic mistake; Caddy cannot read it.
    assert "htpasswd" in smoke.check_reviewers_file(htpasswd)[0]
    # Caddy refuses to start with no reviewer at all.
    assert statuses(smoke.check_reviewers_file(only_comments)) == ["FAIL"]
    assert "does not exist" in smoke.check_reviewers_file(tmp_path / "missing.txt")[0]


def test_ownership_wants_uid_and_gid_10001():
    stat_output = "\n".join(
        [
            "10001 10001 750 /srv/pipeline",
            "0 0 755 /srv/pipeline/inbox",  # the classic: root-owned after mounting
            "MISSING /srv/pipeline/teams.csv",
        ]
    )
    lines = smoke.check_ownership(stat_output, 10001, 10001)
    assert statuses(lines) == ["PASS", "FAIL", "FAIL"]
    # The failure says exactly how to fix it.
    assert "sudo chown 10001:10001 /srv/pipeline/inbox" in lines[1]


def test_inbox_must_be_its_own_filesystem_no_bigger_than_the_cap():
    gib = 1024**3
    # The runbook's 25 GiB loop file, as findmnt -b reports it (a bit less).
    assert statuses(smoke.check_inbox_mount(f"ext4 {24 * gib}", 25)) == ["PASS"]
    # Not mounted: findmnt prints nothing, so the inbox shares the root disk.
    assert statuses(smoke.check_inbox_mount("", 25)) == ["FAIL"]
    # Much bigger than the cap: the disk budget no longer holds.
    assert statuses(smoke.check_inbox_mount(f"ext4 {60 * gib}", 25)) == ["FAIL"]


def test_report_table_and_verdict():
    lines = [
        smoke.result("PASS", "container redis", "healthy"),
        smoke.result("FAIL", "evolution instance state", "state 'close'"),
    ]
    table, all_passed = smoke.render_report(lines)
    assert all_passed is False
    assert "1 CHECK(S) FAILED" in table
    # Each check is one row of the table.
    assert "PASS    container redis" in table
    # And an all-green run says so.
    _, all_green = smoke.render_report(lines[:1])
    assert all_green is True


# --- The shell script, end to end, with fake commands --------------------------

linux_only = pytest.mark.skipif(
    sys.platform != "linux" or shutil.which("bash") is None,
    reason="compose-smoke.sh is a bash script for the Linux VPS (uses GNU stat)",
)

FAKE_DOCKER = """#!/bin/sh
cat "$FAKE_DOCKER_PS"
"""

FAKE_FINDMNT = """#!/bin/sh
cat "$FAKE_FINDMNT_OUTPUT"
"""

# The fake curl finds -o <file> and the URL among its arguments, looks the
# URL up in a JSON file of canned answers, writes the body and prints the
# status code, like `curl -o FILE -w '%{http_code}' URL` does.
FAKE_CURL = """#!{python}
import json, os, sys
args = sys.argv[1:]
output = args[args.index("-o") + 1]
url = [arg for arg in args if arg.startswith("http")][-1]
answers = json.load(open(os.environ["FAKE_CURL_ANSWERS"]))
status, body = answers.get(url, ["000", ""])
open(output, "w").write(body)
sys.stdout.write(status)
"""


def install_fake_commands(bin_dir: Path) -> None:
    bin_dir.mkdir()
    for name, text in {
        "docker": FAKE_DOCKER,
        "findmnt": FAKE_FINDMNT,
        "curl": FAKE_CURL.replace("{python}", sys.executable),
    }.items():
        path = bin_dir / name
        path.write_text(text)
        path.chmod(0o755)


def healthy_vps(tmp_path: Path) -> dict[str, str]:
    """A fake VPS where every check should pass; returns the environment to run with."""
    install_fake_commands(tmp_path / "bin")
    env_file = tmp_path / ".env"
    env_file.write_text(
        "PIPELINE_DOMAIN=review.test\nEVO_DOMAIN=evo.test\nTAILSCALE_IP=100.64.0.1\n"
        "EVOLUTION_API_KEY=k\nBVP_EVOLUTION_INSTANCE=baja\nBVP_ADMIN_TOKEN=admin\n"
        # Single-quoted bcrypt hash, the way README §2.5 says to write it.
        f"MANAGER_BASIC_AUTH_HASH='{a_bcrypt_hash()}'\n"
    )
    data_dir = tmp_path / "srv-pipeline"
    for folder in ("inbox", "proxies", "thumbs", "outbox", "db"):
        (data_dir / folder).mkdir(parents=True)
    (data_dir / "reviewers.txt").write_text(f"ana {a_bcrypt_hash()}\n")
    (data_dir / "teams.csv").write_text("car_number,team_name,school\n")

    docker_ps = tmp_path / "ps.json"
    docker_ps.write_text(
        "\n".join(
            compose_line(service, health="" if service == "caddy" else "healthy")
            for service in smoke.EXPECTED_SERVICES
        )
    )
    findmnt = tmp_path / "findmnt.txt"
    findmnt.write_text(f"ext4 {24 * 1024**3}\n")
    answers = {
        "https://review.test/review/": ["401", ""],
        "https://evo.test/": ["401", "Missing apikey header"],
        "http://100.64.0.1:8080/health": ["200", '{"status":"ok"}'],
        "https://review.test/enroll": ["400", '{"error":{"code":"validation_error"}}'],
        "http://100.64.0.1:8080/metrics": [
            "200",
            'bvp_jobs{state="received"} 0\nbvp_workers_online{role="master"} 1\n',
        ],
        "https://evo.test/instance/connectionState/baja": [
            "200",
            '{"instance":{"instanceName":"baja","state":"open"}}',
        ],
    }
    answers_file = tmp_path / "answers.json"
    answers_file.write_text(json.dumps(answers))
    return {
        **os.environ,
        "PATH": f"{tmp_path / 'bin'}{os.pathsep}{os.environ['PATH']}",
        "SMOKE_ENV_FILE": str(env_file),
        "SMOKE_DATA_DIR": str(data_dir),
        # The temp folder belongs to whoever runs the tests, not to 10001.
        "SMOKE_UID": str(os.getuid()),
        "SMOKE_GID": str(os.getgid()),
        "SMOKE_PYTHON": sys.executable,
        "FAKE_DOCKER_PS": str(docker_ps),
        "FAKE_FINDMNT_OUTPUT": str(findmnt),
        "FAKE_CURL_ANSWERS": str(answers_file),
    }


def run_smoke(environment: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(VPS_FOLDER / "compose-smoke.sh")],
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
    )


@linux_only
def test_the_script_passes_on_a_healthy_vps(tmp_path):
    completed = run_smoke(healthy_vps(tmp_path))
    # Exit code 0 and the final verdict, with every check in the table.
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "ALL CHECKS PASSED" in completed.stdout
    assert "FAIL" not in completed.stdout
    for check in ("container evolution", "caddy 443 review.test", "evolution instance state"):
        assert check in completed.stdout


@linux_only
def test_the_script_fails_and_names_each_problem(tmp_path):
    environment = healthy_vps(tmp_path)
    # Break three things: Evolution lost its session, the outbox folder is
    # missing, and Caddy's tailnet listener does not answer.
    answers = json.loads(Path(environment["FAKE_CURL_ANSWERS"]).read_text())
    answers["https://evo.test/instance/connectionState/baja"] = [
        "200",
        '{"instance":{"state":"close"}}',
    ]
    del answers["http://100.64.0.1:8080/health"]
    Path(environment["FAKE_CURL_ANSWERS"]).write_text(json.dumps(answers))
    (Path(environment["SMOKE_DATA_DIR"]) / "outbox").rmdir()

    completed = run_smoke(environment)

    # Exit code 1 tells a script (or João) that something is wrong...
    assert completed.returncode == 1
    # ...and the table has exactly those three FAIL rows.
    fail_rows = [line for line in completed.stdout.splitlines() if line.startswith("FAIL")]
    assert len(fail_rows) == 3, completed.stdout
    assert any("evolution instance state" in row and "close" in row for row in fail_rows)
    assert any("outbox" in row and "missing" in row for row in fail_rows)
    assert any("/health" in row and "no connection" in row for row in fail_rows)
    assert "3 CHECK(S) FAILED" in completed.stdout


@linux_only
def test_the_script_stops_early_without_a_readable_env_file(tmp_path):
    environment = healthy_vps(tmp_path)
    environment["SMOKE_ENV_FILE"] = str(tmp_path / "no-such.env")
    completed = run_smoke(environment)
    # Exit code 2 = could not even start (as opposed to 1 = checks failed).
    assert completed.returncode == 2
    assert "cannot read" in completed.stderr
