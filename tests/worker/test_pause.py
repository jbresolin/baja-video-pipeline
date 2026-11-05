"""Pausing a worker (LIVESTACK-38 item 9): "Pausar" in the tray or on the status page.

What "paused" must mean, each checked below with the real daemon threads
against the fake orchestrator:
  * the task it is on is finished, not cut off;
  * no new task of any kind is asked for afterwards (no POST /next at all);
  * heartbeats keep going and say `paused: true`;
  * held raws are still served to peers (the :8710 file server);
  * the switch survives a restart (it is stored in worker.json);
  * "Retomar" makes it ask for work again.
"""

import threading

import httpx

from shared.testing.asgi_transport import SyncAsgiTransport
from tests.worker.fakes import FakeMediaTools
from tests.worker.test_daemon import process_task, start_daemon, wait_until
from worker.config import load_config, save_config
from worker.daemon import DaemonTimings

# Beat and poll every 50 ms instead of 15 s / 5 s, so the test sees several
# heartbeats and /next rounds in a fraction of a second.
FAST = DaemonTimings(heartbeat_interval_seconds=0.05, max_dispatch_wait_seconds=0.05)


def test_pause_finishes_the_current_task_then_takes_nothing_new(tmp_path):
    # Set-up: a render that waits until the test lets it finish, so we can
    # press "Pausar" while it is running.
    render_started, may_finish = threading.Event(), threading.Event()
    media = FakeMediaTools()

    def controlled_render(what):
        render_started.set()
        may_finish.wait(10)

    media.during_render = controlled_render
    orchestrator, daemon = start_daemon(tmp_path, media, timings=FAST)
    try:
        first = orchestrator.add_job(b"first raw")
        daemon.storage.raw_path(first.job_id, ".mp4").write_bytes(b"first raw")
        orchestrator.offer(process_task(first))
        assert render_started.wait(10)

        # The volunteer clicks "Pausar" mid-render.
        daemon.pause.pause()
        asks_before = len(orchestrator.next_requests)
        # A second job is waiting; a working worker would take it.
        second = orchestrator.add_job(b"second raw")
        daemon.storage.raw_path(second.job_id, ".mp4").write_bytes(b"second raw")
        orchestrator.offer(process_task(second))

        # Let the first render finish: its clip is uploaded as usual,
        # because pausing never cuts off a task in progress.
        may_finish.set()
        assert wait_until(lambda: orchestrator.stored_upload("outbox", first.job_id).exists())

        # Heartbeats go on, and say "paused".
        beats_now = len(orchestrator.heartbeats)
        assert wait_until(lambda: len(orchestrator.heartbeats) > beats_now)
        assert orchestrator.heartbeats[-1].paused is True

        # But the worker asked for nothing new (the dispatcher may have been
        # mid-request when we paused, hence "at most one more"), and the
        # second job was never processed.
        threading.Event().wait(0.3)  # several dispatcher rounds at 50 ms
        assert len(orchestrator.next_requests) <= asks_before + 1
        assert not orchestrator.stored_upload("outbox", second.job_id).exists()

        # "Retomar": it asks again and does the second job.
        daemon.pause.resume()
        assert wait_until(lambda: orchestrator.stored_upload("outbox", second.job_id).exists())
    finally:
        may_finish.set()
        daemon.stop()


def test_a_paused_worker_still_serves_held_raws_to_peers(tmp_path):
    # The peer file server (:8710) does not care about the pause: other
    # workers may still need this machine's copy.
    orchestrator, daemon = start_daemon(tmp_path, FakeMediaTools())
    try:
        daemon.pause.pause()
        job = orchestrator.add_job(b"held raw bytes")
        daemon.storage.raw_path(job.job_id, ".mp4").write_bytes(b"held raw bytes")
        peer = httpx.Client(transport=SyncAsgiTransport(daemon.http_app()), base_url="http://w")
        response = peer.get(f"/raw/{job.job_id}")
        assert response.status_code == 200
        assert response.content == b"held raw bytes"
    finally:
        daemon.stop()


def test_the_pause_survives_a_restart(tmp_path):
    # First run: the volunteer pauses. The switch writes worker.json.
    config_path = tmp_path / "worker.json"

    def write_config_file(config):
        save_config(config_path, config)

    orchestrator, daemon = start_daemon(tmp_path, FakeMediaTools(), before_start=write_config_file)
    daemon.pause.pause()
    daemon.stop()
    assert load_config(config_path).paused is True

    # Second run (a reboot): the new daemon reads worker.json and starts
    # paused, so its first heartbeat already says so and it asks for nothing.
    orchestrator, daemon = start_daemon(
        tmp_path, FakeMediaTools(), before_start=lambda _config: None
    )
    try:
        assert daemon.pause.is_paused
        assert wait_until(lambda: orchestrator.heartbeats)
        assert orchestrator.heartbeats[0].paused is True
        assert daemon.dispatcher.kinds_to_ask_for() == []
        assert orchestrator.next_requests == []
    finally:
        daemon.stop()
