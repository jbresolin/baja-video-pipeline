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
from worker.dispatcher import ASK_AGAIN_FOR_REFUSED_KIND_SECONDS


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


def test_upload_original_is_asked_for_again_once_the_orchestrator_was_updated(harness):
    # Arrange: the usual upgrade order, workers first and the VPS later. The
    # worker (0.3.0) first meets the old orchestrator, which refuses any
    # /next naming upload_original, so the worker stops asking for it.
    harness.orchestrator.unknown_task_kinds = {"upload_original"}
    harness.dispatcher.run_once()
    assert TaskKind.UPLOAD_ORIGINAL not in harness.dispatcher.kinds_to_ask_for(), (
        "right after the refusal the worker must leave the kind out"
    )

    # Act: the VPS is updated (the orchestrator now knows the kind) and the
    # worker keeps running, without a restart, for five more minutes.
    harness.orchestrator.unknown_task_kinds = set()
    harness.steady_clock.advance(ASK_AGAIN_FOR_REFUSED_KIND_SECONDS)
    harness.dispatcher.run_once()

    # Assert: it asks for upload_original again, so "Baixar original" works
    # without anyone restarting the worker.
    last = harness.orchestrator.next_requests[-1]
    assert TaskKind.UPLOAD_ORIGINAL in last.accept_kinds, (
        "after the waiting time the worker must try the refused kind again"
    )


def test_a_still_old_orchestrator_is_not_asked_again_before_the_wait_is_over(harness):
    # Arrange: an orchestrator that does not know upload_original refuses it once.
    harness.orchestrator.unknown_task_kinds = {"upload_original"}
    harness.dispatcher.run_once()

    # Act: one minute later the worker asks for work again.
    harness.steady_clock.advance(60)
    harness.dispatcher.run_once()

    # Assert: that request leaves upload_original out, so an old VPS is not
    # bothered with a refused request on every round.
    last = harness.orchestrator.next_requests[-1]
    assert TaskKind.UPLOAD_ORIGINAL not in last.accept_kinds, (
        "within the five minutes the refused kind stays out of /next"
    )
