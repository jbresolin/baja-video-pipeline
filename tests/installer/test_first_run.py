"""`BajaWorker.exe --first-run` (installer/baja_installer/first_run.py).

What happens on a volunteer's PC after the installer finishes: the setup
page opens, the service enrolls, and this flow runs `tailscale up` with the
key the service left in tailscale-authkey.txt, then restarts the service.

Nothing real is touched here: `FakeTailscale` stands in for the tailscale
command, and the "service" is simulated by writing worker.json and the key
file the way worker/enrollment.py does. Every outside effect (browser,
service restart, yes/no questions, messages) is recorded in `Recorder`.
"""

import subprocess
from dataclasses import dataclass, field

from baja_installer.first_run import (
    EXIT_CANCELLED,
    EXIT_JOIN_FAILED,
    EXIT_JOINED,
    FirstRunEnvironment,
    run_first_run,
    tailscale_hostname,
)
from baja_installer.tailscale_cli import TailscaleStatus
from shared.models import WorkerRole
from worker.config import WorkerConfig, load_config, save_config
from worker.enrollment import TAILSCALE_AUTH_KEY_FILE_NAME

TAILNET_BASE_URL = "http://100.64.0.1:8080"
WORKER_ID = "1234abcd5678ef90"

OFFLINE = TailscaleStatus("NeedsLogin", (), None, None, None)
OTHER_TAILNET = TailscaleStatus("Running", (), "100.90.0.5", "pc.other.ts.net", "other.ts.net")
ON_BAJA = TailscaleStatus(
    "Running", ("tag:worker",), "100.101.102.103", "baja-w-1234abcd.tail1234.ts.net", "tail"
)


class FakeTailscale:
    """Answers status() from a list (the last answer repeats) and records up()."""

    def __init__(self, statuses, up_returncode=0, up_stderr=""):
        self.statuses = list(statuses)
        self.up_returncode = up_returncode
        self.up_stderr = up_stderr
        self.up_calls = []  # (hostname, switching_tailnet, key file content)

    def status(self):
        return self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]

    def up(self, key_path, hostname, *, switching_tailnet):
        key = key_path.read_text().strip() if key_path.exists() else None
        self.up_calls.append((hostname, switching_tailnet, key))
        return subprocess.CompletedProcess(
            ["tailscale", "up"], self.up_returncode, "", self.up_stderr
        )


@dataclass
class Recorder:
    answers: list[bool] = field(default_factory=list)  # replies to yes/no questions, in order
    questions: list[str] = field(default_factory=list)
    messages: list[str] = field(default_factory=list)
    browser_opened: int = 0
    service_restarts: int = 0
    sleeps: int = 0

    def ask(self, question):
        self.questions.append(question)
        return self.answers.pop(0) if self.answers else False


def enrolled_config(role=WorkerRole.WORKER) -> WorkerConfig:
    """worker.json as worker/enrollment.py leaves it after POST /enroll."""
    return WorkerConfig(
        worker_id=WORKER_ID,
        bearer_token="worker-secret",
        role=role,
        tailnet_base_url=TAILNET_BASE_URL,
        enroll_url="https://enroll.example",
    )


def simulate_service_enrolling(config_path, role=WorkerRole.WORKER):
    save_config(config_path, enrolled_config(role))
    (config_path.parent / TAILSCALE_AUTH_KEY_FILE_NAME).write_text("tskey-auth-SECRET\n")


def make_environment(tmp_path, tailscale, recorder, on_sleep=None):
    config_path = tmp_path / "worker.json"

    def sleep(seconds):
        recorder.sleeps += 1
        if on_sleep:
            on_sleep(config_path)

    def open_page():
        recorder.browser_opened += 1

    def restart():
        recorder.service_restarts += 1

    return FirstRunEnvironment(
        config_path=config_path,
        tailscale=tailscale,
        open_setup_page=open_page,
        restart_service=restart,
        ask_yes_no=recorder.ask,
        say=recorder.messages.append,
        sleep=sleep,
    )


# --- the normal path ---------------------------------------------------------


def test_fresh_install_waits_for_enrollment_then_joins_and_restarts(tmp_path):
    # Set up: no worker.json at first. The first time the flow waits
    # ("sleeps"), the simulated service enrolls and leaves the key file.
    tailscale = FakeTailscale([OFFLINE, ON_BAJA])
    recorder = Recorder()
    environment = make_environment(tmp_path, tailscale, recorder, simulate_service_enrolling)

    exit_code = run_first_run(environment)

    # It opened the setup page once and had to wait at least once.
    assert exit_code == EXIT_JOINED
    assert recorder.browser_opened == 1
    assert recorder.sleeps >= 1
    # tailscale up ran once, with our generic hostname, reading the key
    # the service wrote. No tailnet switch (the PC was logged out).
    assert tailscale.up_calls == [("baja-w-1234abcd", False, "tskey-auth-SECRET")]
    # The key is single-use and secret: it is gone afterwards.
    assert not (tmp_path / TAILSCALE_AUTH_KEY_FILE_NAME).exists()
    # The service was restarted so it picks up the new settings.
    assert recorder.service_restarts == 1


def test_after_joining_worker_json_knows_the_tailnet_address_and_orchestrator(tmp_path):
    tailscale = FakeTailscale([OFFLINE, ON_BAJA])
    environment = make_environment(tmp_path, tailscale, Recorder())
    simulate_service_enrolling(environment.config_path)

    run_first_run(environment)

    config = load_config(environment.config_path)
    # The address peers use to reach this PC's file server (8710).
    assert config.tailnet_address == "100.101.102.103"
    # Every API call now goes to Caddy's tailnet listener from /enroll,
    # instead of the built-in local default http://127.0.0.1:8000.
    assert config.orchestrator_url == TAILNET_BASE_URL
    # The identity is untouched.
    assert config.worker_id == WORKER_ID and config.bearer_token == "worker-secret"


def test_an_orchestrator_url_set_by_hand_is_kept(tmp_path):
    tailscale = FakeTailscale([OFFLINE, ON_BAJA])
    environment = make_environment(tmp_path, tailscale, Recorder())
    simulate_service_enrolling(environment.config_path)
    hand_set = enrolled_config().model_copy(update={"orchestrator_url": "http://baja-vps:8080"})
    save_config(environment.config_path, hand_set)

    run_first_run(environment)

    assert load_config(environment.config_path).orchestrator_url == "http://baja-vps:8080"


def test_the_studio_joins_as_baja_studio(tmp_path):
    # The role comes from the enrollment token; the installer has no switch.
    tailscale = FakeTailscale([OFFLINE, ON_BAJA])
    environment = make_environment(tmp_path, tailscale, Recorder())
    simulate_service_enrolling(environment.config_path, role=WorkerRole.STUDIO)

    run_first_run(environment)

    assert tailscale.up_calls[0][0] == "baja-studio"


def test_hostnames_never_contain_the_pc_or_volunteer_name():
    # infra/tailnet/README.md §2: names can end up in public logs.
    config = enrolled_config().model_copy(update={"worker_name": "Maria's Gaming PC"})
    assert tailscale_hostname(config) == "baja-w-1234abcd"


# --- other tailnets and re-runs ------------------------------------------------


def test_a_pc_on_another_tailnet_is_asked_before_switching(tmp_path):
    # The volunteer already uses Tailscale for something else and says yes.
    tailscale = FakeTailscale([OTHER_TAILNET, ON_BAJA])
    recorder = Recorder(answers=[True])
    environment = make_environment(tmp_path, tailscale, recorder)
    simulate_service_enrolling(environment.config_path)

    assert run_first_run(environment) == EXIT_JOINED

    # One question was asked, and up was told to log in again.
    assert len(recorder.questions) == 1 and "outra rede" in recorder.questions[0]
    assert tailscale.up_calls[0][1] is True


def test_saying_no_changes_nothing_and_keeps_the_key_for_later(tmp_path):
    tailscale = FakeTailscale([OTHER_TAILNET])
    recorder = Recorder(answers=[False])
    environment = make_environment(tmp_path, tailscale, recorder)
    simulate_service_enrolling(environment.config_path)

    assert run_first_run(environment) == EXIT_CANCELLED

    # No join, no restart, and the unused key stays so a re-run can use it.
    assert tailscale.up_calls == []
    assert recorder.service_restarts == 0
    assert (tmp_path / TAILSCALE_AUTH_KEY_FILE_NAME).exists()


def test_a_pc_already_on_the_baja_tailnet_does_not_join_again(tmp_path):
    # e.g. the volunteer ran "concluir conexão" a second time.
    tailscale = FakeTailscale([ON_BAJA])
    recorder = Recorder()
    environment = make_environment(tmp_path, tailscale, recorder)
    save_config(environment.config_path, enrolled_config())  # enrolled, key already used

    assert run_first_run(environment) == EXIT_JOINED

    assert tailscale.up_calls == []
    assert load_config(environment.config_path).tailnet_address == "100.101.102.103"
    assert recorder.service_restarts == 1


# --- failures --------------------------------------------------------------------


def test_a_failed_join_deletes_the_key_and_shows_tailscales_message(tmp_path):
    tailscale = FakeTailscale([OFFLINE], up_returncode=1, up_stderr="invalid key: expired")
    recorder = Recorder(answers=[False])  # "erase the registration?" → no
    environment = make_environment(tmp_path, tailscale, recorder)
    simulate_service_enrolling(environment.config_path)

    assert run_first_run(environment) == EXIT_JOIN_FAILED

    # The key is deleted even though the join failed (worker_join.md §5).
    assert not (tmp_path / TAILSCALE_AUTH_KEY_FILE_NAME).exists()
    # The volunteer sees why, and that a new token is needed.
    assert any("invalid key: expired" in line for line in recorder.messages)
    assert any("novo token" in line for line in recorder.messages)
    # They said "no" to erasing: the identity is still there.
    assert load_config(environment.config_path).is_enrolled is True


def test_after_a_failed_join_the_volunteer_can_erase_the_registration(tmp_path):
    tailscale = FakeTailscale([OFFLINE], up_returncode=1, up_stderr="boom")
    recorder = Recorder(answers=[True])  # "erase the registration?" → yes
    environment = make_environment(tmp_path, tailscale, recorder)
    simulate_service_enrolling(environment.config_path)

    run_first_run(environment)

    # Back to "not enrolled", so the setup page shows the token form again,
    # and the service was restarted to notice.
    config = load_config(environment.config_path)
    assert config.is_enrolled is False
    assert config.bearer_token is None
    # Settings that are not identity (enroll URL) survive.
    assert config.enroll_url == "https://enroll.example"
    assert recorder.service_restarts == 1


def test_a_join_that_reports_success_but_lands_elsewhere_is_a_failure(tmp_path):
    # tailscale up exits 0 but status shows no Baja tag (wrong key type).
    tailscale = FakeTailscale([OFFLINE, OTHER_TAILNET])
    environment = make_environment(tmp_path, tailscale, Recorder())
    simulate_service_enrolling(environment.config_path)

    assert run_first_run(environment) == EXIT_JOIN_FAILED
    assert load_config(environment.config_path).tailnet_address is None


def test_enrolled_without_a_key_and_not_on_the_tailnet_needs_a_new_token(tmp_path):
    # The key was used (or deleted) but the PC never made it onto the
    # tailnet: there is no way forward with this enrollment.
    tailscale = FakeTailscale([OFFLINE])
    recorder = Recorder(answers=[False])
    environment = make_environment(tmp_path, tailscale, recorder)
    save_config(environment.config_path, enrolled_config())

    assert run_first_run(environment) == EXIT_JOIN_FAILED
    assert tailscale.up_calls == []
