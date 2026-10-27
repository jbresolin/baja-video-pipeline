"""The daemon as a whole: threads start, work flows, and shutdown is clean.

Unlike the other tests, these run the real background threads (heartbeat,
dispatcher, stages), so they wait for things with a short polling helper.
The HTTP servers are not started here; the "network" is still the
in-memory RoutingTransport. test_daemon_in_process.py covers the servers
(on 127.0.0.1 only) and the knobs an in-process end-to-end test uses.
"""

import threading
import time

from shared.models import ProcessTask, WorkerRole
from tests.worker.conftest import OUTBOX_DELIVERY
from tests.worker.fake_orchestrator import (
    BASE_URL,
    BEARER_TOKEN,
    TAILNET_URL,
    WORKER_ID,
    FakeOrchestrator,
)
from tests.worker.fakes import FakeMediaTools, RoutingTransport
from worker.config import WorkerConfig
from worker.daemon import Daemon, DaemonTimings
from worker.status import StatusBoard


def wait_until(condition, timeout=10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.02)
    return False


def start_daemon(
    tmp_path,
    media,
    *,
    role=WorkerRole.WORKER,
    start_servers=False,
    timings: DaemonTimings | None = None,
    before_start=None,
    **config_changes,
):
    """Build and start a daemon against a fake orchestrator.

    The daemon's "network" (daemon.env.transfer_transport) is an in-memory
    RoutingTransport. `before_start(config)` runs just before start(), to
    put files in the working folder as an earlier run would have.
    """
    orchestrator = FakeOrchestrator(tmp_path / "vps", role=role)
    network = RoutingTransport()
    network.add_app(BASE_URL, orchestrator.app)
    network.add_app(TAILNET_URL, orchestrator.app)
    config = WorkerConfig(
        orchestrator_url=BASE_URL,
        tailnet_base_url=TAILNET_URL,
        worker_id=WORKER_ID,
        bearer_token=BEARER_TOKEN,
        role=role,
        working_folder=tmp_path / "work",
        tailnet_address="worker.test",
        **config_changes,
    )
    if before_start is not None:
        before_start(config)
    shutdown = threading.Event()
    daemon = Daemon(
        config,
        tmp_path / "worker.json",
        StatusBoard(),
        shutdown,
        media=media,
        transport=network,
        start_servers=start_servers,
        timings=timings,
    )
    daemon.start()
    return orchestrator, daemon


def process_task(job) -> ProcessTask:
    return ProcessTask(
        job_id=job.job_id,
        raw_sha256=job.sha256,
        has_audio=True,
        low_res=False,
        cut_in_s=1.0,
        cut_out_s=5.0,
        prova="Enduro",
        car_number=7,
        team_name=None,
        passagem=1,
        show_pass=True,
        clip_relative_path="enduro/7/1.mp4",
        outbox_upload_url=f"{BASE_URL}/jobs/{job.job_id}/clip",
        delivery=OUTBOX_DELIVERY,
    )


def test_daemon_heartbeats_and_processes_an_offered_job(tmp_path):
    orchestrator, daemon = start_daemon(tmp_path, FakeMediaTools())
    try:
        # The first heartbeat goes out as soon as the daemon starts.
        assert wait_until(lambda: orchestrator.heartbeats)
        # A held raw and a process offer: the daemon picks it up by itself.
        job = orchestrator.add_job(b"raw video bytes")
        daemon.storage.raw_path(job.job_id, ".mp4").write_bytes(b"raw video bytes")
        orchestrator.offer(process_task(job))
        assert wait_until(lambda: orchestrator.stored_upload("outbox", job.job_id).exists())
    finally:
        daemon.stop()


def test_shutdown_mid_render_stops_promptly_and_leaves_no_partial_files(tmp_path):
    # Setup: a render that "takes forever" (until the task is cancelled).
    render_started = threading.Event()
    media = FakeMediaTools()

    def slow_render(what):
        render_started.set()
        while not daemon.shutdown.is_set():
            time.sleep(0.01)

    media.during_render = slow_render
    orchestrator, daemon = start_daemon(tmp_path, media)
    job = orchestrator.add_job(b"raw video bytes")
    daemon.storage.raw_path(job.job_id, ".mp4").write_bytes(b"raw video bytes")
    orchestrator.offer(process_task(job))
    assert render_started.wait(10)

    # The service manager asks us to stop (SIGTERM / CTRL-C → shutdown).
    started = time.monotonic()
    daemon.stop()

    # Every thread ended quickly...
    assert time.monotonic() - started < 10
    assert all(not stage._thread.is_alive() for stage in daemon.stages.values())
    # ...nothing was uploaded or reported as failed...
    assert not orchestrator.stored_upload("outbox", job.job_id).exists()
    assert orchestrator.failures == []
    # ...tmp/ has no half-rendered clip or overlay, no clip was published,
    # and the held raw is untouched.
    assert list(daemon.storage.tmp_dir.iterdir()) == []
    assert not daemon.storage.clip_path(job.job_id).exists()
    assert daemon.storage.find_raw(job.job_id).read_bytes() == b"raw video bytes"
