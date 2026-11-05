"""Tests for the review version (`Job.version`, docs/api.md §4).

The first real video showed the problem: while a worker was busy with a
job (confirming it holds the raw, uploading the proxy, renewing leases)
every one of those updates bumped `version`, so the reviewer who had the
page open got "someone changed this job" on every save.

The rule now: `version` only goes up when a *reviewer* changes the job
(save, ready, reprocess, discard, restore, retry). Machine activity never
touches it. These tests open the "page" (read the version), let machines
work on the job, and check that the reviewer's save still goes through,
while two reviewers editing at once still conflict.
"""

from orchestrator.testing.scenarios import (
    claim_ok,
    hold_raw,
    make_proxy,
    receive_job,
    review_to_ready,
)
from shared.models import TaskKind, WorkerRole


def save(harness, job_id, expected_version, **fields):
    """PATCH …/review with action=save, as the review page's Save button does."""
    return harness.client.patch(
        f"/jobs/{job_id}/review",
        json={"expected_version": expected_version, "action": "save", **fields},
        auth=harness.reviewer_auth,
    )


def test_worker_activity_while_the_page_is_open_does_not_conflict(harness):
    # Set-up: a master and a second worker, and a job just received.
    master = harness.enroll(WorkerRole.MASTER, "master", tailnet_address="100.70.0.9")
    helper = harness.enroll(WorkerRole.WORKER, "helper", tailnet_address="100.70.0.10")
    job_id = receive_job(harness)
    # The reviewer opens the job page now: this is the version the page keeps.
    version_on_the_page = harness.job(job_id)["version"]

    # Machines work on the job: the master confirms it holds the raw,
    # uploads thumbnail and proxy (the job moves to awaiting_review), the
    # second worker confirms too (the VPS drops its copy), and a heartbeat
    # renews nothing in particular.
    hold_raw(harness, master, job_id)
    make_proxy(harness, master, job_id)
    hold_raw(harness, helper, job_id)
    harness.heartbeat(master)
    assert harness.job(job_id)["state"] == "awaiting_review"

    # The reviewer saves with the version they loaded. Before the fix this
    # was a 409 version_conflict; now it must succeed because nobody else
    # *reviewed* the job in between.
    response = save(harness, job_id, version_on_the_page, prova="Enduro")
    assert response.status_code == 200, response.text
    # Their save is a review change, so it bumps the version by exactly one.
    assert response.json()["version"] == version_on_the_page + 1


def test_processing_a_ready_job_does_not_conflict_with_a_save(harness):
    # A ready job claimed for processing (a lease, state claimed) is still
    # editable with "save"; claiming must not bump the version.
    master = harness.enroll(WorkerRole.MASTER, "master", tailnet_address="100.70.0.9")
    job_id = receive_job(harness)
    hold_raw(harness, master, job_id)
    make_proxy(harness, master, job_id)
    version_after_review = review_to_ready(harness, job_id)["version"]

    claim_ok(harness, master, job_id, TaskKind.PROCESS)
    assert harness.job(job_id)["state"] == "claimed"

    response = save(harness, job_id, version_after_review, show_pass=False)
    assert response.status_code == 200, response.text


def test_two_reviewers_editing_the_same_job_still_conflict(harness):
    # Set-up: a job waiting for review.
    master = harness.enroll(WorkerRole.MASTER, "master", tailnet_address="100.70.0.9")
    job_id = receive_job(harness)
    hold_raw(harness, master, job_id)
    make_proxy(harness, master, job_id)
    # Both reviewers open the page and see the same version.
    seen_by_both = harness.job(job_id)["version"]

    # The first reviewer saves: fine.
    first = save(harness, job_id, seen_by_both, prova="Enduro")
    assert first.status_code == 200

    # The second reviewer saves with the now-stale version: 409, so they
    # cannot silently overwrite the first reviewer's prova.
    second = save(harness, job_id, seen_by_both, prova="Suspensão")
    assert second.status_code == 409
    error = second.json()["error"]
    assert error["code"] == "version_conflict"
    assert error["details"]["current_version"] == seen_by_both + 1
