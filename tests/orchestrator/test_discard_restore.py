"""Tests for discard and restore (LIVESTACK-38 item 4: a discard can be undone).

A reviewer discarded a good video by mistake during the first real event.
The rules now (orchestrator/domain/discard.py):

* discarding deletes nothing at once: for the grace period (24 h by
  default) the holders keep the raw and the VPS its inbox copy;
* "restore" puts the job back in the state it was discarded from;
* after the grace period holders are told to delete (deletable_job_ids)
  and the inbox copy goes; restore then works only while some copy is left.

The harness clock is manual, so "24 hours later" is one call.
"""

import pytest

from orchestrator.background.sweepers import clean_inbox_once
from orchestrator.testing.harness import build_harness
from orchestrator.testing.scenarios import hold_raw, make_proxy, receive_job, review_to_ready
from shared.models import WorkerRole

DAY_SECONDS = 24 * 3600


@pytest.fixture
def job_in_review(harness):
    """A job waiting for review, held only by the master (so the inbox still has it)."""
    master = harness.enroll(WorkerRole.MASTER, "master", tailnet_address="100.70.0.9")
    job_id = receive_job(harness)
    hold_raw(harness, master, job_id)
    make_proxy(harness, master, job_id)
    return master, job_id


def review(harness, job_id, action, **fields):
    body = {"expected_version": harness.job(job_id)["version"], "action": action, **fields}
    return harness.client.patch(f"/jobs/{job_id}/review", json=body, auth=harness.reviewer_auth)


def test_within_the_grace_period_nothing_is_deleted(harness, job_in_review):
    master, job_id = job_in_review
    inbox_file = harness.context.layout.inbox_path(job_id, ".mp4")

    discarded = review(harness, job_id, "discard").json()

    # The job is discarded and says until when its raws are kept.
    assert discarded["state"] == "discarded"
    assert discarded["discard_grace_ends_at"] is not None
    # One hour later: the VPS copy is still there, even after a cleanup
    # pass, and the master is NOT told to delete its copy.
    harness.clock.advance(3600)
    clean_inbox_once(harness.context)
    assert inbox_file.exists()
    assert harness.heartbeat(master, held_job_ids=[job_id])["deletable_job_ids"] == []


def test_restore_within_the_grace_period_goes_back_to_awaiting_review(harness, job_in_review):
    master, job_id = job_in_review
    review(harness, job_id, "discard")
    harness.clock.advance(3600)

    response = review(harness, job_id, "restore")

    # Back exactly where it was, ready to be reviewed again; the discard
    # bookkeeping is cleared.
    assert response.status_code == 200, response.text
    job = response.json()
    assert job["state"] == "awaiting_review"
    assert job["discarded_from"] is None
    assert job["discard_grace_ends_at"] is None
    # The master still holds the raw, and is never told to delete it.
    assert harness.heartbeat(master, held_job_ids=[job_id])["deletable_job_ids"] == []


def test_a_ready_job_is_restored_to_ready(harness, job_in_review):
    _, job_id = job_in_review
    review_to_ready(harness, job_id)
    review(harness, job_id, "discard")
    # "Where it was discarded from" was ready (reviewed, waiting for processing).
    assert review(harness, job_id, "restore").json()["state"] == "ready"


def test_after_the_grace_period_holders_delete_and_the_inbox_is_freed(harness, job_in_review):
    master, job_id = job_in_review
    inbox_file = harness.context.layout.inbox_path(job_id, ".mp4")
    review(harness, job_id, "discard")

    harness.clock.advance(DAY_SECONDS + 1)

    # The cleanup pass drops the VPS copy...
    assert clean_inbox_once(harness.context) == [job_id]
    assert not inbox_file.exists()
    # ...and the master's next heartbeat says it may delete its raw.
    assert harness.heartbeat(master, held_job_ids=[job_id])["deletable_job_ids"] == [job_id]


def test_after_the_grace_period_restore_works_while_a_holder_still_has_the_raw(
    harness, job_in_review
):
    # The master was offline when the grace period ended, so it still has
    # (and still lists) the raw: restore is allowed, and the job will wait
    # for the master like any job whose holder is offline.
    _, job_id = job_in_review
    review(harness, job_id, "discard")
    harness.clock.advance(DAY_SECONDS + 1)
    clean_inbox_once(harness.context)

    response = review(harness, job_id, "restore")

    assert response.status_code == 200, response.text
    assert response.json()["state"] == "awaiting_review"


def test_after_the_grace_period_restore_is_refused_when_no_copy_is_left(harness, job_in_review):
    master, job_id = job_in_review
    review(harness, job_id, "discard")
    harness.clock.advance(DAY_SECONDS + 1)
    clean_inbox_once(harness.context)
    # The master deleted its raw: its next heartbeat no longer lists it,
    # which drops it as a holder. Now nobody has the raw.
    harness.heartbeat(master, held_job_ids=[])

    response = review(harness, job_id, "restore")

    # 409 raw_unavailable: the UI explains that the raw is gone.
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "raw_unavailable"
    assert harness.job(job_id)["state"] == "discarded"


def test_restore_is_only_for_discarded_jobs(harness, job_in_review):
    _, job_id = job_in_review
    response = review(harness, job_id, "restore")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "illegal_transition"


def test_the_grace_period_is_a_setting(tmp_path):
    # A 1-hour grace period: two hours after the discard, the raws may go.
    harness = build_harness(tmp_path, discard_grace_hours=1)
    master = harness.enroll(WorkerRole.MASTER, "master", tailnet_address="100.70.0.9")
    job_id = receive_job(harness)
    hold_raw(harness, master, job_id)
    make_proxy(harness, master, job_id)
    review(harness, job_id, "discard")
    harness.clock.advance(2 * 3600)
    assert harness.heartbeat(master, held_job_ids=[job_id])["deletable_job_ids"] == [job_id]
