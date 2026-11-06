"""Why the worker is waiting for the tailnet (worker.tailnet_wait, LIVESTACK-41 item 1).

tailnet_problem_for() turns two observations into one of three sentences
for the status page: does this PC have the saved address (can we bind a
socket to it?), and what does `tailscale status` say? Both are passed in
as fakes here, so the tests need no Tailscale.
"""

import threading

from shared.models import WorkerRole
from shared.testing.credentials import PLACEHOLDER_WORKER_TOKEN
from worker.config import WorkerConfig, save_config
from worker.status import Phase, StatusBoard, TailnetProblemKind
from worker.tailnet_wait import tailnet_problem_for, wait_for_tailnet, waiter
from worker.tailscale import TailscaleStatus, address_is_local

SAVED = "100.64.0.9"


def tailscale(state: str, ipv4: str | None = None) -> TailscaleStatus:
    return TailscaleStatus(state, ("tag:worker",), ipv4, None, None)


def test_no_problem_when_the_address_is_on_this_pc():
    assert tailnet_problem_for(SAVED, is_local=lambda a: True, read_status=None) is None


def test_tailscale_not_answering_yet():
    # Right after a boot the Tailscale service may not answer for a while.
    problem = tailnet_problem_for(SAVED, is_local=lambda a: False, read_status=lambda: None)
    assert problem.kind == TailnetProblemKind.TAILSCALE_NOT_ANSWERING
    assert problem.saved_address == SAVED


def test_tailscale_logged_out_means_not_connected():
    # E.g. the device was removed from the tailnet in the admin console.
    problem = tailnet_problem_for(
        SAVED, is_local=lambda a: False, read_status=lambda: tailscale("NeedsLogin")
    )
    assert problem.kind == TailnetProblemKind.NOT_CONNECTED


def test_tailscale_running_with_another_address_says_the_address_is_not_ours():
    problem = tailnet_problem_for(
        SAVED, is_local=lambda a: False, read_status=lambda: tailscale("Running", "100.70.1.2")
    )
    assert problem.kind == TailnetProblemKind.ADDRESS_NOT_ON_THIS_PC
    assert problem.current_address == "100.70.1.2"


def test_listen_address_wins_over_the_tailnet_address(tmp_path):
    # If someone set listen_address by hand, that is what the server binds,
    # so that is the address to wait for.
    config_path = tmp_path / "worker.json"
    save_config(
        config_path,
        WorkerConfig(
            worker_id="w",
            bearer_token=PLACEHOLDER_WORKER_TOKEN,
            role=WorkerRole.WORKER,
            tailnet_address="100.1.1.1",
            listen_address="127.0.0.1",
        ),
    )
    checked = []

    def check(address):
        checked.append(address)
        return None

    status = StatusBoard()
    config = wait_for_tailnet(
        config_path, status, waiter(threading.Event(), threading.Event()), check=check
    )

    assert config is not None
    assert checked == ["127.0.0.1"]
    assert status.facts().tailnet_problem is None


def test_before_the_join_there_is_no_address_and_nothing_to_wait_for(tmp_path):
    config_path = tmp_path / "worker.json"
    save_config(config_path, WorkerConfig())
    status = StatusBoard()

    config = wait_for_tailnet(config_path, status, lambda seconds: True, check=None)

    assert config is not None
    assert status.facts().phase == Phase.STARTING  # never entered the waiting phase


def test_address_is_local_on_a_real_socket():
    # 127.0.0.1 exists on every machine; 192.0.2.1 is reserved for
    # documentation and is never assigned to a real interface.
    assert address_is_local("127.0.0.1") is True
    assert address_is_local("192.0.2.1") is False
