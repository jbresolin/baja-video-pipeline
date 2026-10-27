"""Running child processes: exit codes, stderr tail, and stopping mid-run.

These use the Python interpreter itself as the "child program", so they
need no ffmpeg and still exercise the real process handling.
"""

import sys
import threading
import time

import pytest

from worker.leases import REASON_SHUTDOWN, CancelToken, TaskCancelledError
from worker.media_tools import MediaToolError, MediaTools, run_cancellable_process


def python(code: str) -> list[str]:
    return [sys.executable, "-c", code]


def test_success_returns_exit_code_zero():
    exit_code, _ = run_cancellable_process(python("pass"), CancelToken(None, threading.Event()))
    assert exit_code == 0


def test_failure_returns_exit_code_and_last_stderr_lines():
    code = "import sys\nfor n in range(100): print('line', n, file=sys.stderr)\nsys.exit(3)"
    exit_code, stderr_tail = run_cancellable_process(
        python(code), CancelToken(None, threading.Event())
    )
    assert exit_code == 3
    # Only the last lines are kept: enough to explain, small enough to report.
    assert "line 99" in stderr_tail and "line 50" not in stderr_tail


def test_shutdown_terminates_a_long_running_process_quickly():
    # Setup: a "render" that would take 60 s; shutdown is requested 0.3 s in.
    shutdown = threading.Event()
    threading.Timer(0.3, shutdown.set).start()
    started = time.monotonic()

    with pytest.raises(TaskCancelledError) as raised:
        run_cancellable_process(python("import time; time.sleep(60)"), CancelToken(None, shutdown))

    # It stopped within a couple of seconds, not 60, and says why.
    assert time.monotonic() - started < 5
    assert raised.value.reason == REASON_SHUTDOWN


def test_run_ffmpeg_turns_a_bad_exit_into_a_media_tool_error():
    tools = MediaTools(sys.executable, sys.executable, "libx264")
    with pytest.raises(MediaToolError) as raised:
        tools.run_ffmpeg(
            python("import sys; print('Invalid data found', file=sys.stderr); sys.exit(1)"),
            CancelToken(None, threading.Event()),
            what="proxy",
        )
    assert raised.value.exit_code == 1
    assert "Invalid data found" in raised.value.message
