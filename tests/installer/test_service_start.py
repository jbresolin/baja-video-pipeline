"""`BajaWorker.exe --service` waits for the Tailscale address
(installer/baja_installer/service_start.py).

At boot the service can start before Tailscale has its address. These
tests fake both the clock and the "is this address on the PC?" check, so
they run instantly.
"""

from baja_installer.service_start import address_is_local, wait_for_tailnet_address
from worker.config import WorkerConfig, save_config


class FakeClock:
    """time.monotonic() and time.sleep() that only move when we sleep."""

    def __init__(self):
        self.now = 0.0

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def write_config(tmp_path, **settings):
    path = tmp_path / "worker.json"
    save_config(path, WorkerConfig(**settings))
    return path


def test_before_enrollment_there_is_nothing_to_wait_for(tmp_path):
    # No tailnet_address in worker.json: start the worker (setup page) at once.
    config_path = write_config(tmp_path)
    checked = []

    assert wait_for_tailnet_address(config_path, is_local=checked.append) is True
    assert checked == []  # never even looked


def test_the_worker_starts_once_the_address_appears(tmp_path):
    config_path = write_config(tmp_path, tailnet_address="100.101.102.103")
    clock = FakeClock()
    # Pretend Tailscale needs about 10 seconds to bring its adapter up.
    appears_at = 10.0

    started = wait_for_tailnet_address(
        config_path,
        is_local=lambda address: clock.now >= appears_at,
        clock=clock.time,
        sleep=clock.sleep,
        poll_seconds=3.0,
        say=lambda line: None,
    )

    # It waited (the clock moved past 10 s) and then said "go".
    assert started is True
    assert clock.now >= appears_at


def test_it_gives_up_after_the_timeout_so_nssm_restarts_it(tmp_path):
    config_path = write_config(tmp_path, tailnet_address="100.101.102.103")
    clock = FakeClock()
    messages = []

    started = wait_for_tailnet_address(
        config_path,
        timeout_seconds=30.0,
        is_local=lambda address: False,  # never appears
        clock=clock.time,
        sleep=clock.sleep,
        say=messages.append,
    )

    # False makes cli.py exit with EXIT_TAILNET_NOT_READY; NSSM then starts
    # the service again after its delay.
    assert started is False
    assert clock.now >= 30.0
    assert "did not appear" in messages[-1]


def test_listen_address_wins_over_the_tailnet_address(tmp_path):
    # If someone set listen_address by hand, that is what the server binds.
    config_path = write_config(tmp_path, tailnet_address="100.1.1.1", listen_address="127.0.0.1")
    checked = []

    def is_local(address):
        checked.append(address)
        return True

    wait_for_tailnet_address(config_path, is_local=is_local)
    assert checked == ["127.0.0.1"]


def test_address_is_local_on_a_real_socket():
    # 127.0.0.1 exists on every machine; 192.0.2.1 is reserved for
    # documentation and is never assigned to a real interface.
    assert address_is_local("127.0.0.1") is True
    assert address_is_local("192.0.2.1") is False
