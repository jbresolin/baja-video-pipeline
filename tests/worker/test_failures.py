"""The failure-scope table (worker.failures, design §17).

POST /jobs/{id}/failed carries `scope`:
  "job"     the input is bad; the job fails at once
  "worker"  this worker could not do it; the task goes to another worker
These tests check the one table that decides it, error type by error type,
plus the two cases where a network failure is not reported at all. The
end-to-end behaviour (which task raises what) is tested in the task tests.
"""

import pytest

from shared.models import FailureScope, TaskKind, WorkerRole
from tests.worker.fakes import FakeMediaTools
from worker.failures import (
    InvalidTaskError,
    SourceHashMismatchError,
    TaskFailedError,
    TransferGaveUpError,
    UndecodableInputError,
    failure_message_for,
    failure_scope_for,
    is_expected_failure,
    probe_input,
    should_report_failure,
)
from worker.media_tools import MediaToolError


@pytest.mark.parametrize(
    "error",
    [
        SourceHashMismatchError("every source sent the wrong bytes"),
        UndecodableInputError("ffprobe cannot read the raw"),
        InvalidTaskError("unsafe clip path"),
    ],
)
def test_bad_input_blames_the_job(error):
    assert failure_scope_for(error) == FailureScope.JOB


@pytest.mark.parametrize(
    "error",
    [
        MediaToolError("ffmpeg proxy failed with exit code 1", 1),  # local ffmpeg
        TransferGaveUpError("outbox upload failed"),  # network gave up
        OSError(28, "No space left on device"),  # disk full
        PermissionError("raws/ is read-only"),  # an OSError subclass
        TaskFailedError("the raw is not on this worker's disk"),  # other local problem
        KeyError("oops"),  # a bug in the worker
    ],
)
def test_everything_else_blames_this_worker(error):
    assert failure_scope_for(error) == FailureScope.WORKER


def test_network_failures_of_the_master_download_and_studio_deliver_are_not_reported():
    # Only the master can be "the master holder", and there is one studio:
    # a "worker" report would exclude them from that task on that job, so
    # their network failures leave the lease to expire instead.
    network = TransferGaveUpError("no source reachable")
    assert not should_report_failure(network, WorkerRole.MASTER, TaskKind.DOWNLOAD_RAW)
    assert not should_report_failure(network, WorkerRole.STUDIO, TaskKind.DELIVER)
    # Every other combination is reported.
    assert should_report_failure(network, WorkerRole.WORKER, TaskKind.DOWNLOAD_RAW)
    assert should_report_failure(network, WorkerRole.MASTER, TaskKind.PROCESS)
    # And other errors of those same tasks are reported as usual.
    bad_hash = SourceHashMismatchError("bad bytes")
    assert should_report_failure(bad_hash, WorkerRole.MASTER, TaskKind.DOWNLOAD_RAW)


def test_messages_are_readable_for_the_reviewer():
    # Errors we raise on purpose keep their own words; others say what happened.
    assert failure_message_for(MediaToolError("ffmpeg said no", 1), TaskKind.PROCESS) == (
        "ffmpeg said no"
    )
    disk = failure_message_for(OSError(28, "No space left on device"), TaskKind.PROCESS)
    assert disk.startswith("local disk/file error during process")
    bug = failure_message_for(KeyError("x"), TaskKind.MAKE_PROXY)
    assert bug.startswith("internal worker error during make_proxy")
    # The API refuses an empty message, so there is always some text.
    assert failure_message_for(TaskFailedError(""), TaskKind.DELIVER) == "deliver failed"


def test_only_unplanned_errors_count_as_bugs():
    # Bugs get a full traceback in the log; planned failures do not.
    assert is_expected_failure(TransferGaveUpError("x"))
    assert is_expected_failure(OSError("x"))
    assert not is_expected_failure(KeyError("x"))


def test_probe_input_turns_an_unreadable_raw_into_a_job_failure(tmp_path):
    # ffprobe failing on the raw means the raw is bad, whatever machine runs it.
    media = FakeMediaTools()
    media.probe_error = MediaToolError("Invalid data found when processing input", 1)
    with pytest.raises(UndecodableInputError) as raised:
        probe_input(media, tmp_path / "raw.mp4")
    assert failure_scope_for(raised.value) == FailureScope.JOB
    assert raised.value.exit_code == 1
