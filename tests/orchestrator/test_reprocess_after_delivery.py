"""After delivery: the outdated-clips queue filter and "Salvar e reprocessar" (LIVESTACK-43).

Continues test_render_inputs.py (same fixtures and helpers):

* GET /review/queue?outdated=true lists exactly the jobs whose clip no
  longer matches the review;
* "Salvar e reprocessar" is one PATCH (action "reprocess" with the
  fields): the job goes back to ready, and the next rendering is version
  2, delivered to the studio as "…-v2.mp4" so the clip on air is never
  written over;
* with no holder of the raw online the reprocess is still accepted, and
  the job says it waits for one.
"""

import pytest

from orchestrator.testing.scenarios import claim_ok, upload_in_chunks
from shared.models import TaskKind
from tests.orchestrator.test_render_inputs import (
    OFFLINE_SECONDS,
    edit_review,
    enroll_fleet,
    ready_job,
    render_and_push,
)


@pytest.fixture
def fleet(harness):
    """The master and the studio, 26BR teams synced (see test_render_inputs.py)."""
    return enroll_fleet(harness)


# --- The queue filter "Clipes desatualizados" ---


def test_the_queue_lists_only_outdated_clips(harness, fleet):
    master, studio = fleet
    fresh = ready_job(harness, master, car_number=1)
    edited = ready_job(harness, master, car_number=1, passagem=4)
    for job_id in (fresh, edited):
        render_and_push(harness, master, studio, job_id)
    edit_review(harness, edited, cut_in_s=2.0)

    def queue(**params):
        response = harness.client.get(
            "/review/queue", params={"state": ["done"], **params}, auth=harness.reviewer_auth
        )
        assert response.status_code == 200, response.text
        return {item["id"]: item["clip_outdated"] for item in response.json()["items"]}

    assert queue() == {fresh: False, edited: True}
    assert queue(outdated="true") == {edited: True}


# --- "Salvar e reprocessar": one request, then version 2 ---


def test_save_and_reprocess_is_one_request_and_yields_version_two(harness, fleet):
    master, studio = fleet
    job_id = ready_job(harness, master, cut_in_s=3.0, cut_out_s=12.0)
    first = render_and_push(harness, master, studio, job_id, clip=b"first" * 300)
    assert first["clip_relative_path"] == "enduro/1/enduro-001-p3.mp4"

    # One PATCH saves the new cut and sends the job back to ready.
    job = edit_review(harness, job_id, action="reprocess", cut_in_s=5.0)
    assert job["state"] == "ready"
    assert job["cut_in_s"] == 5.0
    # Until the new version arrives, the box still plays version 1, which
    # does not match the review: the warning stays up meanwhile.
    assert job["clip_inputs"]["version"] == 1
    assert job["clip_inputs"]["changed"] == ["cut"]

    # The next rendering is version 2, sent under its own name so the
    # clip on air (enduro-001-p3.mp4) is never written over.
    second = render_and_push(harness, master, studio, job_id, clip=b"second" * 300)
    assert second["clip_relative_path"] == "enduro/1/enduro-001-p3-v2.mp4"
    assert second["cut_in_s"] == 5.0

    job = harness.job(job_id)
    assert job["state"] == "done"
    assert job["clip"]["version"] == 2
    assert job["clip"]["relative_path"] == "enduro/1/enduro-001-p3-v2.mp4"
    assert job["clip"]["download_file_name"] == "enduro-001-p3-v2.mp4"
    # The version keeps the first name; its archive name is built from it.
    assert [v["archive_relative_path"] for v in job["clip_versions"]] == [
        "enduro/1/enduro-001-p3.mp4",
        "enduro/1/enduro-001-p3-v2.mp4",
    ]
    assert job["clip_inputs"] == {**job["clip_inputs"], "version": 2, "changed": []}
    manifest = harness.client.get("/manifest", auth=harness.reviewer_auth).json()["clips"]
    assert [(entry["relative_path"], entry["version"]) for entry in manifest] == [
        ("enduro/1/enduro-001-p3-v2.mp4", 2)
    ]


def test_a_reprocess_through_the_outbox_is_also_delivered_as_v2(harness, fleet):
    master, studio = fleet
    job_id = ready_job(harness, master)
    render_and_push(harness, master, studio, job_id, clip=b"first" * 300)
    edit_review(harness, job_id, action="reprocess", cut_in_s=2.0)
    lease_id, _ = claim_ok(harness, master, job_id, TaskKind.PROCESS)
    upload_in_chunks(harness, master, f"/jobs/{job_id}/clip", lease_id, b"second" * 300)
    # The studio pulls it from the outbox and stores it where the task says.
    _, deliver_task = claim_ok(harness, studio, job_id, TaskKind.DELIVER)
    assert deliver_task["clip_relative_path"] == "enduro/1/enduro-001-p3-v2.mp4"


def test_reprocess_with_no_holder_online_is_accepted_and_waits(harness, fleet):
    master, studio = fleet
    job_id = ready_job(harness, master)
    render_and_push(harness, master, studio, job_id)
    harness.clock.advance(OFFLINE_SECONDS)  # the master (the only holder) goes offline

    job = edit_review(harness, job_id, action="reprocess", cut_in_s=2.0)
    assert job["state"] == "ready"
    # The existing waiting-reason sentence, which the job page shows in pt-BR.
    assert job["waiting_reason"] == (
        "waiting for a holder of the raw to come back online (or be resumed)"
    )
