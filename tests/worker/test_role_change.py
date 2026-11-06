"""The worker takes its new role from the heartbeat answer (LIVESTACK-42 item 8).

Background for a beginner: an admin can press "Tornar master" on a worker
in the review UI; that worker becomes the master and the old master a
plain worker. Nobody edits worker.json for that. Each heartbeat sends the
role the worker is running as and the answer says the role the
orchestrator has for it. When they differ, the worker writes the new role
into worker.json and stops cleanly; as a Windows service it is started
again by NSSM 10 s later, now in the new role.

Only master ↔ worker switch this way: a studio has another tailnet tag.
"""

from shared.models import WorkerRole
from tests.worker.fakes import FakeMediaTools
from tests.worker.test_daemon import start_daemon, wait_until
from worker.config import WorkerConfig, load_config, save_config
from worker.daemon import DaemonTimings
from worker.role_change import adopt_role_from_heartbeat


class RestartRecorder:
    """Stands in for "stop the worker": remembers whether it was asked."""

    def __init__(self) -> None:
        self.asked = False

    def __call__(self) -> None:
        self.asked = True


def saved_worker(tmp_path, role: WorkerRole):
    """An enrolled worker.json with some installer settings that must survive."""
    path = tmp_path / "worker.json"
    save_config(
        path,
        WorkerConfig(
            worker_id="w-1",
            bearer_token="token-for-tests",
            role=role,
            ffmpeg_path="C:/BajaWorker/ffmpeg.exe",
            enroll_url="https://baja.example.org",
            storage_cap_bytes=123,
        ),
    )
    return path


# --- The decision (worker/role_change.py) ---


def test_a_worker_made_master_saves_the_role_and_restarts(tmp_path):
    path = saved_worker(tmp_path, WorkerRole.WORKER)
    restart = RestartRecorder()

    switched = adopt_role_from_heartbeat(path, WorkerRole.WORKER, WorkerRole.MASTER, restart)

    assert switched is True
    assert restart.asked
    config = load_config(path)
    assert config.role == WorkerRole.MASTER
    # Everything else the installer and the volunteer wrote is kept.
    assert config.ffmpeg_path == "C:/BajaWorker/ffmpeg.exe"
    assert config.enroll_url == "https://baja.example.org"
    assert config.storage_cap_bytes == 123
    assert (config.worker_id, config.bearer_token) == ("w-1", "token-for-tests")


def test_the_old_master_becomes_a_worker_the_same_way(tmp_path):
    path = saved_worker(tmp_path, WorkerRole.MASTER)
    restart = RestartRecorder()

    adopt_role_from_heartbeat(path, WorkerRole.MASTER, WorkerRole.WORKER, restart)

    assert restart.asked
    assert load_config(path).role == WorkerRole.WORKER


def test_the_same_role_or_no_role_changes_nothing(tmp_path):
    # The usual case on every beat, and an older orchestrator without `role`.
    path = saved_worker(tmp_path, WorkerRole.WORKER)
    restart = RestartRecorder()

    assert adopt_role_from_heartbeat(path, WorkerRole.WORKER, WorkerRole.WORKER, restart) is False
    assert adopt_role_from_heartbeat(path, WorkerRole.WORKER, None, restart) is False

    assert not restart.asked
    assert load_config(path).role == WorkerRole.WORKER


def test_a_studio_never_switches_by_itself(tmp_path):
    # A studio has another tailnet tag: it must enroll again, never switch.
    studio = saved_worker(tmp_path, WorkerRole.STUDIO)
    restart = RestartRecorder()

    assert adopt_role_from_heartbeat(studio, WorkerRole.STUDIO, WorkerRole.MASTER, restart) is False
    assert adopt_role_from_heartbeat(studio, WorkerRole.WORKER, WorkerRole.STUDIO, restart) is False

    assert not restart.asked
    assert load_config(studio).role == WorkerRole.STUDIO


# --- The whole daemon, against the fake orchestrator ---


def test_the_daemon_sends_its_role_and_restarts_when_made_master(tmp_path):
    # A running worker whose worker.json is on disk, heartbeating fast.
    timings = DaemonTimings(heartbeat_interval_seconds=0.05)
    orchestrator, daemon = start_daemon(
        tmp_path,
        FakeMediaTools(),
        timings=timings,
        before_start=lambda config: save_config(tmp_path / "worker.json", config),
    )
    try:
        # Every heartbeat says which role it runs as.
        assert wait_until(lambda: orchestrator.heartbeats)
        assert orchestrator.heartbeats[0].role == WorkerRole.WORKER
        assert not daemon.shutdown.is_set()

        # An admin makes it the master: the next answer says so.
        orchestrator.role = WorkerRole.MASTER

        # The worker saves the role and stops (to be started again by NSSM).
        assert wait_until(daemon.shutdown.is_set, timeout=5)
        assert load_config(tmp_path / "worker.json").role == WorkerRole.MASTER
    finally:
        daemon.stop()
