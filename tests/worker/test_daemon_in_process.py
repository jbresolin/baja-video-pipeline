"""Running whole daemons inside one test process (for end-to-end tests).

An end-to-end test runs a master, a worker and a studio as threads in one
process. These tests check the pieces that make that possible:
  * a server port of 0 means "any free port", and heartbeats report the
    port really bound (the orchestrator builds peer and studio URLs from it);
  * DaemonTimings shortens the real waits (heartbeat interval, pause
    between /next rounds, ACK retry);
  * the studio replays pending_acks.json from its working folder at start;
  * http_app() gives the role's app for mounting on an in-memory transport.
Servers here listen on 127.0.0.1 only and are stopped at the end.
"""

import json

import httpx

from shared.models import WorkerRole
from tests.worker.fakes import FakeMediaTools
from tests.worker.test_daemon import start_daemon, wait_until
from worker.daemon import DaemonTimings
from worker.studio.pending_acks import PENDING_ACKS_FILE_NAME


def test_port_zero_binds_a_free_port_and_heartbeats_report_it(tmp_path):
    # Setup: a studio whose receiver should listen on "any free port" of
    # 127.0.0.1, as an in-process test with several workers needs.
    orchestrator, daemon = start_daemon(
        tmp_path,
        FakeMediaTools(),
        role=WorkerRole.STUDIO,
        start_servers=True,
        listen_address="127.0.0.1",
        receiver_port=0,
    )
    try:
        # A real port was chosen, and the receiver answers on it.
        port = daemon.listening_port
        assert port and port > 0
        health = httpx.get(f"http://127.0.0.1:{port}/health", trust_env=False).json()
        assert health == {"status": "ok", "worker_id": "worker-1", "role": "studio"}
        # The heartbeat tells the orchestrator that port, not 0, so the
        # orchestrator can hand workers a working studio_url.
        assert wait_until(lambda: orchestrator.heartbeats)
        assert orchestrator.heartbeats[0].receiver_port == port
    finally:
        daemon.stop()


def test_peer_file_server_on_port_zero_serves_held_raws(tmp_path):
    orchestrator, daemon = start_daemon(
        tmp_path,
        FakeMediaTools(),
        start_servers=True,
        listen_address="127.0.0.1",
        file_server_port=0,
    )
    try:
        daemon.storage.raw_path("job-1", ".mp4").write_bytes(b"raw bytes")
        port = daemon.listening_port
        # A peer (or the master) can fetch the raw from the real socket.
        answer = httpx.get(f"http://127.0.0.1:{port}/raw/job-1", trust_env=False)
        assert answer.status_code == 200 and answer.content == b"raw bytes"
        assert wait_until(lambda: orchestrator.heartbeats)
        assert orchestrator.heartbeats[0].file_server_port == port
    finally:
        daemon.stop()


def test_heartbeat_interval_can_be_shortened(tmp_path):
    # The orchestrator says "every 15 s"; DaemonTimings overrides that so
    # lease news (e.g. "lost" after a studio ACK) arrives within a moment.
    timings = DaemonTimings(heartbeat_interval_seconds=0.05)
    orchestrator, daemon = start_daemon(tmp_path, FakeMediaTools(), timings=timings)
    try:
        assert wait_until(lambda: len(orchestrator.heartbeats) >= 3, timeout=5)
    finally:
        daemon.stop()


def test_dispatcher_pause_can_be_capped(tmp_path):
    # The fake orchestrator answers "nothing for you, retry in 1 s". With
    # the cap at 0.05 s the daemon asks again much sooner, so a job offered
    # a moment later is picked up without waiting out the full second.
    timings = DaemonTimings(max_dispatch_wait_seconds=0.05)
    orchestrator, daemon = start_daemon(tmp_path, FakeMediaTools(), timings=timings)
    try:
        assert wait_until(lambda: len(orchestrator.next_requests) >= 5, timeout=0.9)
    finally:
        daemon.stop()


def test_studio_sends_acks_left_in_its_working_folder_at_start(tmp_path):
    # Setup: an earlier run of the studio stored a pushed clip but stopped
    # before its delivered ACK got through; the ACK is in pending_acks.json.
    def earlier_run_left_an_ack(config):
        config.working_folder.mkdir(parents=True)
        owed = [
            {
                "job_id": "job-1",
                "relative_path": "enduro/1/1.mp4",
                "sha256": "a" * 64,
                "size_bytes": 10,
                "in_playlist": True,
            }
        ]
        (config.working_folder / PENDING_ACKS_FILE_NAME).write_text(json.dumps(owed))

    orchestrator, daemon = start_daemon(
        tmp_path,
        FakeMediaTools(),
        role=WorkerRole.STUDIO,
        before_start=earlier_run_left_an_ack,
    )
    try:
        # The studio's ACK thread sends it without any new push...
        assert wait_until(lambda: orchestrator.delivered)
        assert orchestrator.delivered[0][0] == "job-1"
        # ...and the file no longer lists it.
        pending_file = daemon.config.working_folder / PENDING_ACKS_FILE_NAME
        assert wait_until(lambda: json.loads(pending_file.read_text()) == [])
    finally:
        daemon.stop()


def test_http_app_can_be_mounted_without_a_socket(tmp_path):
    # Instead of real ports, a test may put the role's app on its
    # in-memory transport under any address it likes.
    orchestrator, daemon = start_daemon(tmp_path, FakeMediaTools())
    try:
        network = daemon.env.transfer_transport
        network.add_app("http://worker.test:8710", daemon.http_app())
        client = httpx.Client(transport=network)
        assert client.get("http://worker.test:8710/health").json()["role"] == "worker"
    finally:
        daemon.stop()
