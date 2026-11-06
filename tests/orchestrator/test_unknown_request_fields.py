"""The orchestrator still refuses requests with fields it does not know.

Design §14 splits the shared models by direction. What a client READS
from the orchestrator ignores unknown fields, so an older worker keeps
working when the orchestrator adds one (see tests/worker and
tests/review_ui test_newer_orchestrator.py). What the orchestrator
RECEIVES stays strict: a misspelt or made-up field is a client bug, and it
fails loudly with 400 validation_error instead of being silently dropped.
"""

from shared.models import WorkerRole


def assert_refused_for(response, field: str) -> None:
    assert response.status_code == 400, response.text
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    # The message names the field that was not allowed.
    assert field in response.text, error


def test_a_heartbeat_with_an_unknown_field_is_refused(harness):
    worker = harness.enroll(WorkerRole.WORKER, "pc")
    body = {"free_bytes": 1, "storage_cap_bytes": 2, "free_byts": 1}  # a typo

    response = harness.client.post(
        f"/workers/{worker.worker_id}/heartbeat", json=body, headers=worker.headers
    )

    assert_refused_for(response, "free_byts")


def test_an_unknown_field_inside_a_nested_request_object_is_refused(harness):
    worker = harness.enroll(WorkerRole.WORKER, "pc")
    held_clip = {"job_id": "j1", "sha256": "a" * 64, "version": 2}  # no such field
    body = {"free_bytes": 1, "storage_cap_bytes": 2, "held_clips": [held_clip]}

    response = harness.client.post(
        f"/workers/{worker.worker_id}/heartbeat", json=body, headers=worker.headers
    )

    assert_refused_for(response, "version")


def test_a_review_save_with_an_unknown_field_is_refused(harness):
    # The body is checked before the job is even looked up, so any id will do.
    response = harness.client.patch(
        "/jobs/any-job/review",
        json={"expected_version": 1, "action": "save", "car": 42},  # car_number, misspelt
        auth=harness.reviewer_auth,
    )

    assert_refused_for(response, "car")
