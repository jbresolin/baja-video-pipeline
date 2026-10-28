"""Tests for infra/vps/up.sh, down.sh and their helper infra/vps/stack_ctl.py.

up.sh and down.sh bring the pipeline stack up and down next to the base
stack (README §6). We cannot run Docker, Caddy or Evolution here, so:

* the first group calls stack_ctl.py's webhook decisions directly;
* the rest runs the real shell scripts with fake `docker` and `curl`
  programs first on PATH. The fake curl plays Evolution and remembers the
  webhook between calls, so running a script twice shows whether it is
  really idempotent. Every call to either fake is logged, so the tests can
  check what was (and was not) done.
"""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

VPS_FOLDER = Path(__file__).resolve().parents[2] / "infra" / "vps"
WEBHOOK_URL = "http://bvp-orchestrator:8000/intake/evolution-webhook"
SECRET = "s3cret-webhook-value"


def load_helper():
    """stack_ctl.py is a script, not a package: load it from its file."""
    spec = importlib.util.spec_from_file_location("stack_ctl", VPS_FOLDER / "stack_ctl.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


stack = load_helper()


def stored(**overrides) -> dict:
    """A webhook as Evolution's GET /webhook/find returns it once up.sh set it."""
    webhook = {
        "enabled": True,
        "url": WEBHOOK_URL,
        "events": ["MESSAGES_UPSERT", "CONNECTION_UPDATE"],
        "headers": {"X-Webhook-Secret": SECRET},
        "webhookByEvents": False,
        "webhookBase64": False,
    }
    return {**webhook, **overrides}


# --- Webhook decisions ------------------------------------------------------------


def test_up_sets_a_missing_or_wrong_webhook_and_leaves_a_right_one():
    assert stack.decide_up({}, WEBHOOK_URL, SECRET)[0] == "set"
    assert stack.decide_up(stored(), WEBHOOK_URL, SECRET)[0] == "ok"
    # Each way it can be wrong is named, so the operator sees what changed.
    for change, words in [
        ({"enabled": False}, "disabled"),
        ({"url": "http://old-consumer/hook"}, "old-consumer"),
        ({"events": ["MESSAGES_UPSERT"]}, "events"),
        ({"webhookBase64": True}, "base64"),
        ({"headers": {"X-Webhook-Secret": "rotated"}}, "X-Webhook-Secret"),
        ({"headers": None}, "X-Webhook-Secret"),
    ]:
        action, message = stack.decide_up(stored(**change), WEBHOOK_URL, SECRET)
        assert action == "set" and words in message, change


def test_down_disables_only_our_own_enabled_webhook():
    assert stack.decide_down(stored(), WEBHOOK_URL)[0] == "disable"
    assert stack.decide_down(stored(enabled=False), WEBHOOK_URL)[0] == "none"
    assert stack.decide_down({}, WEBHOOK_URL)[0] == "none"
    # Someone pointed it at another consumer: not ours to switch off.
    assert stack.decide_down(stored(url="http://test-consumer/x"), WEBHOOK_URL)[0] == "foreign"


def test_find_answers_evolution_gives_for_no_webhook_mean_none():
    assert stack.parse_find("") == {}
    assert stack.parse_find("null") == {}
    # Wrapped like the set body: unwrapped.
    assert stack.parse_find(json.dumps({"webhook": stored()}))["url"] == WEBHOOK_URL


def test_webhook_body_is_the_nested_shape_evolution_2_3_7_validates():
    body = stack.webhook_body(True, WEBHOOK_URL, SECRET)
    assert body == {
        "webhook": {
            "enabled": True,
            "url": WEBHOOK_URL,
            "byEvents": False,
            # base64 on would buffer every 2 GB document in Evolution's RAM.
            "base64": False,
            "events": ["MESSAGES_UPSERT", "CONNECTION_UPDATE"],
            "headers": {"X-Webhook-Secret": SECRET},
        }
    }
    # Disabling still sends a url (required) but no secret.
    off = stack.webhook_body(False, WEBHOOK_URL, SECRET)["webhook"]
    assert off["enabled"] is False and off["url"] == WEBHOOK_URL and "headers" not in off


def test_up_sh_configures_the_route_and_header_the_contract_declares():
    contract = yaml.safe_load((VPS_FOLDER.parents[1] / "orchestrator" / "openapi.yaml").read_text())
    path = WEBHOOK_URL.removeprefix("http://bvp-orchestrator:8000")
    assert "post" in contract["paths"][path]
    scheme = contract["components"]["securitySchemes"]["webhookSecret"]
    assert scheme["name"] == stack.SECRET_HEADER
    # The URL in the shell scripts is the one these tests use.
    for script in ("stack_lib.sh", "compose-smoke.sh"):
        assert f'WEBHOOK_URL="{WEBHOOK_URL}"' in (VPS_FOLDER / script).read_text()


# --- The scripts, end to end, with fake commands -------------------------------------

linux_only = pytest.mark.skipif(
    sys.platform != "linux" or shutil.which("bash") is None or shutil.which("envsubst") is None,
    reason="up.sh/down.sh are bash scripts for the Linux VPS (need envsubst)",
)

# Fake docker. FAKE_NETWORK=missing / FAKE_CADDY=missing|stopped simulate a
# base stack that is not up; FAKE_RELOAD_FAIL=1 makes `caddy reload` fail.
FAKE_DOCKER = """#!{python}
import os, sys
args = sys.argv[1:]
with open(os.environ["FAKE_LOG"], "a") as log:
    log.write("docker " + " ".join(args) + "\\n")
if args[:2] == ["network", "inspect"]:
    sys.exit(1 if os.environ.get("FAKE_NETWORK") == "missing" else 0)
if args[0] == "inspect":
    caddy = os.environ.get("FAKE_CADDY", "running")
    if caddy == "missing":
        sys.exit(1)
    print("true" if caddy == "running" else "false")
    sys.exit(0)
if args[0] == "exec" and "reload" in args:
    sys.exit(1 if os.environ.get("FAKE_RELOAD_FAIL") == "1" else 0)
if args[0] == "compose":
    sys.exit(0)
sys.exit("fake docker: unexpected call " + " ".join(args))
"""

# Fake curl playing the orchestrator's /health and Evolution's webhook API.
# The webhook lives in FAKE_EVOLUTION (JSON, "null" = none). FAKE_HEALTH is
# the /health status; FAKE_EVOLUTION_DOWN=1 makes Evolution unreachable.
FAKE_CURL = """#!{python}
import json, os, sys
args = sys.argv[1:]
output = args[args.index("-o") + 1]
url = [arg for arg in args if arg.startswith("http")][-1]
method = args[args.index("-X") + 1] if "-X" in args else "GET"
with open(os.environ["FAKE_LOG"], "a") as log:
    log.write(f"curl {method} {url} " + " ".join(a for a in args if a.startswith("apikey")) + "\\n")
state_file = os.environ["FAKE_EVOLUTION"]
status, body = "000", ""
if url.endswith("/health"):
    status = os.environ.get("FAKE_HEALTH", "200")
    body = '{"status":"ok"}'
elif os.environ.get("FAKE_EVOLUTION_DOWN") == "1":
    pass
elif "apikey: instance-token" not in args:
    status = "401"
elif "/webhook/find/baja" in url:
    status, body = "200", open(state_file).read()
elif "/webhook/set/baja" in url and method == "POST":
    sent = json.load(open(args[args.index("--data-binary") + 1].lstrip("@")))["webhook"]
    # Stored the way GET /webhook/find gives it back.
    row = {
        "enabled": sent["enabled"],
        "url": sent["url"],
        "events": sent["events"],
        "headers": sent.get("headers"),
        "webhookByEvents": sent["byEvents"],
        "webhookBase64": sent["base64"],
    }
    open(state_file, "w").write(json.dumps(row))
    with open(os.environ["FAKE_LOG"], "a") as log:
        log.write("evolution stored " + json.dumps(row) + "\\n")
    status, body = "201", json.dumps({"webhook": row})
open(output, "w").write(body)
sys.stdout.write(status)
"""

FAKE_SMOKE = """#!/bin/sh
echo "SMOKE TABLE"
exit "${FAKE_SMOKE_EXIT:-0}"
"""


def make_vps(tmp_path: Path) -> dict[str, str]:
    """A VPS with the base stack up and the pipeline down; returns the env to run with."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, text in {
        "docker": FAKE_DOCKER.replace("{python}", sys.executable),
        "curl": FAKE_CURL.replace("{python}", sys.executable),
        "smoke": FAKE_SMOKE,
    }.items():
        (bin_dir / name).write_text(text)
        (bin_dir / name).chmod(0o755)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "PIPELINE_DOMAIN=review.test\nTAILSCALE_IP=100.64.0.1\n"
        "EVOLUTION_URL=https://evo.test\nBVP_EVOLUTION_INSTANCE=baja\n"
        f"BVP_EVOLUTION_API_KEY=instance-token\nBVP_WEBHOOK_SECRET={SECRET}\n"
        "BVP_ADMIN_TOKEN=admin\nCADDY_PIPELINE_DIR=/mnt/pipeline\n"
    )
    confd = tmp_path / "conf.d"
    confd.mkdir()
    evolution = tmp_path / "evolution-webhook.json"
    evolution.write_text("null")
    return {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "STACK_ENV_FILE": str(env_file),
        "STACK_CONFD_DIR": str(confd),
        "STACK_PYTHON": sys.executable,
        "STACK_SMOKE": str(bin_dir / "smoke"),
        "STACK_WAIT_SECONDS": "3",
        "STACK_POLL_SECONDS": "0",
        "FAKE_LOG": str(tmp_path / "calls.log"),
        "FAKE_EVOLUTION": str(evolution),
    }


def run(script: str, environment: dict[str, str]) -> subprocess.CompletedProcess:
    Path(environment["FAKE_LOG"]).write_text("")  # one run's calls at a time
    return subprocess.run(
        ["bash", str(VPS_FOLDER / script)],
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
    )


def calls(environment: dict[str, str]) -> str:
    return Path(environment["FAKE_LOG"]).read_text()


def installed(environment: dict[str, str]) -> Path:
    return Path(environment["STACK_CONFD_DIR"]) / "pipeline.caddy"


def webhook(environment: dict[str, str]):
    return json.loads(Path(environment["FAKE_EVOLUTION"]).read_text())


@linux_only
def test_up_on_a_fresh_vps_does_every_step(tmp_path):
    environment = make_vps(tmp_path)

    completed = run("up.sh", environment)

    assert completed.returncode == 0, completed.stdout + completed.stderr
    log = calls(environment)
    assert "docker compose" in log and " pull" in log and " up -d" in log
    # The routes are rendered for this domain and the base Caddy's view of
    # the data dir; Caddy's own placeholders are untouched.
    routes = installed(environment).read_text()
    assert "\nreview.test {" in routes
    assert "root * /mnt/pipeline" in routes
    assert "import /mnt/pipeline/reviewers.txt" in routes
    assert "{http.auth.user.id}" in routes
    assert "${" not in routes
    assert "docker exec -w /etc/caddy caddy caddy reload --config /etc/caddy/Caddyfile" in log
    # The webhook now points at the orchestrator, with the secret header.
    assert webhook(environment) == stored()
    # The smoke test ran last.
    assert "SMOKE TABLE" in completed.stdout


@linux_only
def test_secret_never_appears_in_any_command_line(tmp_path):
    environment = make_vps(tmp_path)
    run("up.sh", environment)
    command_lines = [
        line for line in calls(environment).splitlines() if not line.startswith("evolution")
    ]
    assert not any(SECRET in line for line in command_lines)


@linux_only
def test_up_twice_changes_nothing_the_second_time(tmp_path):
    environment = make_vps(tmp_path)
    run("up.sh", environment)
    first_routes = installed(environment).read_text()

    again = run("up.sh", environment)

    assert again.returncode == 0, again.stdout + again.stderr
    assert "unchanged" in again.stdout
    assert "already points at the orchestrator" in again.stdout
    # No second POST to Evolution; the routes file is the same.
    assert "/webhook/set/" not in calls(environment)
    assert installed(environment).read_text() == first_routes


@linux_only
def test_up_rewrites_routes_when_the_domain_changes(tmp_path):
    environment = make_vps(tmp_path)
    run("up.sh", environment)
    env_file = Path(environment["STACK_ENV_FILE"])
    env_file.write_text(env_file.read_text().replace("review.test", "videos.test"))

    completed = run("up.sh", environment)

    assert completed.returncode == 0
    assert "installed" in completed.stdout
    assert "\nvideos.test {" in installed(environment).read_text()


@linux_only
@pytest.mark.parametrize(
    ("variable", "value", "words"),
    [("FAKE_NETWORK", "missing", "network baja-edge"), ("FAKE_CADDY", "stopped", "caddy")],
)
def test_up_refuses_without_the_base_stack(tmp_path, variable, value, words):
    environment = {**make_vps(tmp_path), variable: value}

    completed = run("up.sh", environment)

    assert completed.returncode == 2
    assert words in completed.stderr and "base stack" in completed.stderr
    # Nothing was started, installed or configured.
    assert "compose" not in calls(environment)
    assert not installed(environment).exists()
    assert webhook(environment) is None


@linux_only
def test_up_takes_the_routes_back_out_when_caddy_refuses_them(tmp_path):
    # A refused file left in conf.d would make every later reload of the
    # base Caddy fail too, for the other stacks as well.
    environment = {**make_vps(tmp_path), "FAKE_RELOAD_FAIL": "1"}

    completed = run("up.sh", environment)

    assert completed.returncode == 2
    assert "removed again" in completed.stderr
    assert not installed(environment).exists()
    # It stopped there: Evolution keeps not posting to us.
    assert webhook(environment) is None


@linux_only
def test_up_puts_the_previous_routes_back_when_caddy_refuses_new_ones(tmp_path):
    environment = make_vps(tmp_path)
    run("up.sh", environment)
    previous = installed(environment).read_text()
    env_file = Path(environment["STACK_ENV_FILE"])
    env_file.write_text(env_file.read_text().replace("review.test", "videos.test"))

    completed = run("up.sh", {**environment, "FAKE_RELOAD_FAIL": "1"})

    assert completed.returncode == 2
    assert "put back" in completed.stderr
    assert installed(environment).read_text() == previous


@linux_only
def test_up_stops_if_the_orchestrator_never_answers(tmp_path):
    environment = {**make_vps(tmp_path), "FAKE_HEALTH": "502"}

    completed = run("up.sh", environment)

    assert completed.returncode == 2
    assert "no /health" in completed.stderr and "HTTP 502" in completed.stderr
    # Public routes and the webhook only go live once it answers.
    assert not installed(environment).exists()
    assert webhook(environment) is None


@linux_only
def test_up_passes_on_a_failed_smoke_check(tmp_path):
    environment = {**make_vps(tmp_path), "FAKE_SMOKE_EXIT": "1"}
    completed = run("up.sh", environment)
    # 1 = the stack is up, but read the table.
    assert completed.returncode == 1
    assert installed(environment).exists()


@linux_only
def test_down_undoes_up_and_a_second_down_has_nothing_to_do(tmp_path):
    environment = make_vps(tmp_path)
    run("up.sh", environment)

    completed = run("down.sh", environment)

    assert completed.returncode == 0, completed.stdout + completed.stderr
    log = calls(environment)
    # Order matters: no more webhooks, then no more public routes, then stop.
    webhook_off = log.index("/webhook/set/")
    reload = log.index("caddy reload")
    compose_down = log.index("down --remove-orphans")
    assert webhook_off < reload < compose_down
    # Disabled but kept, still with our URL (Evolution requires one).
    assert webhook(environment)["enabled"] is False
    assert webhook(environment)["url"] == WEBHOOK_URL
    assert not installed(environment).exists()
    # Volumes are kept: never `down -v`.
    assert " -v" not in log

    again = run("down.sh", environment)

    assert again.returncode == 0
    assert "already disabled" in again.stdout and "already gone" in again.stdout
    log = calls(environment)
    assert "/webhook/set/" not in log and "caddy reload" not in log
    assert "down --remove-orphans" in log


@linux_only
def test_down_leaves_a_webhook_pointed_elsewhere_alone(tmp_path):
    environment = make_vps(tmp_path)
    Path(environment["FAKE_EVOLUTION"]).write_text(json.dumps(stored(url="http://other/hook")))

    completed = run("down.sh", environment)

    assert completed.returncode == 0
    assert "left alone" in completed.stdout
    assert webhook(environment)["enabled"] is True


@linux_only
def test_down_still_stops_everything_when_evolution_is_unreachable(tmp_path):
    environment = make_vps(tmp_path)
    run("up.sh", environment)

    completed = run("down.sh", {**environment, "FAKE_EVOLUTION_DOWN": "1"})

    # 1 = down, but the webhook may still be on: the operator must look.
    assert completed.returncode == 1
    assert "webhook may still be enabled" in completed.stdout
    assert not installed(environment).exists()
    assert "down --remove-orphans" in calls(environment)


@linux_only
def test_down_without_a_running_base_caddy_just_removes_the_file(tmp_path):
    environment = make_vps(tmp_path)
    run("up.sh", environment)

    completed = run("down.sh", {**environment, "FAKE_CADDY": "missing"})

    assert completed.returncode == 0
    assert not installed(environment).exists()
    assert "caddy reload" not in calls(environment)
