"""upload_original: a holder sends the raw a reviewer asked for (LIVESTACK-41 item 15).

The orchestrator offers this task to a worker that holds the raw; the
worker uploads it in chunks (the same protocol as clips) under the task's
lease. These tests run the real task against the in-memory fake
orchestrator (tests/worker/fake_orchestrator.py).
"""

from shared.hashing import sha256_of_bytes
from shared.models import TaskKind, UploadOriginalTask
from tests.worker.fake_orchestrator import BASE_URL
from tests.worker.test_process import hold_raw


def original_task(job) -> UploadOriginalTask:
    return UploadOriginalTask(
        job_id=job.job_id,
        raw_sha256=job.sha256,
        size_bytes=len(job.raw_bytes),
        upload_url=f"{BASE_URL}/jobs/{job.job_id}/original-upload",
    )


def test_the_held_raw_is_uploaded_whole_and_verified(harness, raw_bytes):
    job = hold_raw(harness, raw_bytes)

    harness.claim_and_run(original_task(job))

    stored = harness.orchestrator.stored_upload("originals", job.job_id)
    assert sha256_of_bytes(stored.read_bytes()) == job.sha256
    assert harness.orchestrator.failures == []
    # The raw stays where it is: the VPS copy is only for the reviewer.
    assert harness.storage.find_raw(job.job_id) is not None


def test_a_raw_missing_from_disk_is_reported_so_another_holder_is_asked(harness, raw_bytes):
    job = harness.orchestrator.add_job(raw_bytes, prova="Enduro", car_number=42, passagem=3)

    harness.claim_and_run(original_task(job))

    [(_, failure)] = harness.orchestrator.failures
    assert failure.scope == "worker"
    assert "not on this worker's disk" in failure.message


def test_an_orchestrator_without_upload_original_does_not_block_other_work(harness):
    # A 0.3.0 worker may meet an orchestrator from before item 15, which
    # answers 400 validation_error to any /next naming upload_original.
    # The worker then stops asking for that kind and carries on.
    harness.orchestrator.unknown_task_kinds = {"upload_original"}

    harness.dispatcher.run_once()  # refused once...
    harness.dispatcher.run_once()  # ...then asks without it, and is answered

    assert TaskKind.UPLOAD_ORIGINAL not in harness.dispatcher.kinds_to_ask_for()
    last = harness.orchestrator.next_requests[-1]
    assert TaskKind.PROCESS in last.accept_kinds
