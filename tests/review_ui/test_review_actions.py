"""Tests for the review actions the job page sends (PATCH /review/api/jobs/{id}/review).

The page script sends JSON to the UI; the UI checks it and relays it to the
orchestrator's PATCH /jobs/{id}/review as the same reviewer. These tests
check what reaches the (fake) orchestrator and what the page gets back,
including the 409 "someone else changed it" path.

Setup (conftest.py): "job-awaiting-1" at version 3 awaiting review,
"job-failed-1" at version 3 failed, "job-done-1" at version 3 done.
"""

from shared.models import JobState

REVIEW_URL = "/review/api/jobs/{}/review"


def patch_review(ui, job_id, body):
    return ui.patch(REVIEW_URL.format(job_id), json=body)


def test_save_relays_fields_and_expected_version_to_the_orchestrator(ui, fake_orchestrator):
    body = {
        "expected_version": 3,
        "action": "save",
        "prova": "Enduro",
        "car_number": 42,
        "cut_in_s": 1.5,
    }

    response = patch_review(ui, "job-awaiting-1", body)

    assert response.status_code == 200
    # The orchestrator received one PATCH with exactly what the page sent.
    # (passagem, cut_out_s etc. were not sent, so they keep their values.)
    [sent] = fake_orchestrator.requests_to("PATCH", "/jobs/job-awaiting-1/review")
    assert sent.json_body == body
    # The answer carries the updated job, with the new version the page
    # must use for its next request, and a message to show.
    answer = response.json()
    assert answer["job"]["version"] == 4
    assert answer["job"]["prova"] == "Enduro"
    # The fake resolves car 42 to its team, like the real orchestrator.
    assert answer["job"]["team_name"] == "Equipe Cerrado"
    assert answer["message"] == "Saved."


def test_ready_moves_the_job_to_ready(ui, fake_orchestrator):
    body = {
        "expected_version": 3,
        "action": "ready",
        "prova": "Enduro",
        "car_number": 42,
        "passagem": 1,
        "cut_in_s": 2.0,
        "cut_out_s": 30.0,
    }

    response = patch_review(ui, "job-awaiting-1", body)

    assert response.status_code == 200
    assert fake_orchestrator.jobs["job-awaiting-1"].state == JobState.READY
    assert "Marked ready" in response.json()["message"]


def test_stale_version_gives_409_and_tells_the_page_to_reload(ui, fake_orchestrator):
    # Setup: another reviewer saved first, so the job is now at version 4,
    # while this page still thinks it is at version 3.
    patch_review(ui, "job-awaiting-1", {"expected_version": 3, "action": "save", "prova": "X"})

    response = patch_review(
        ui, "job-awaiting-1", {"expected_version": 3, "action": "save", "prova": "Y"}
    )

    # The orchestrator's 409 is passed on unchanged…
    assert response.status_code == 409
    answer = response.json()
    assert answer["error"]["code"] == "version_conflict"
    assert answer["error"]["details"]["current_version"] == 4
    # …plus reload=true, which makes the page reload and tell the reviewer.
    assert answer["reload"] is True
    # And the other reviewer's value was not overwritten.
    assert fake_orchestrator.jobs["job-awaiting-1"].prova == "X"


def test_action_not_allowed_in_the_state_gives_409_with_reload(ui):
    # "ready" is only for jobs awaiting review; this job is already done.
    response = patch_review(ui, "job-done-1", {"expected_version": 3, "action": "ready"})

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "illegal_transition"
    assert response.json()["reload"] is True


def test_missing_fields_are_listed_so_the_page_can_highlight_them(ui):
    # Ready without passagem and cut out: the orchestrator refuses with 422.
    body = {
        "expected_version": 3,
        "action": "ready",
        "prova": "Enduro",
        "car_number": 42,
        "cut_in_s": 2.0,
    }

    response = patch_review(ui, "job-awaiting-1", body)

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "missing_review_fields"
    assert error["details"]["missing"] == ["passagem", "cut_out_s"]
    # Not a "reload" situation: the reviewer just fills in the fields.
    assert response.json()["reload"] is False


def test_cut_out_before_cut_in_is_refused_without_calling_the_orchestrator(ui, fake_orchestrator):
    body = {"expected_version": 3, "action": "save", "cut_in_s": 10.0, "cut_out_s": 5.0}

    response = patch_review(ui, "job-awaiting-1", body)

    # The UI validates with the same ReviewUpdate model the API uses…
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_error"
    assert "cut_out_s" in response.json()["error"]["message"]
    # …so the bad request never reached the orchestrator.
    assert fake_orchestrator.requests_to("PATCH", "/jobs/job-awaiting-1/review") == []


def test_unknown_fields_are_refused(ui):
    # The API rejects unknown fields (extra="forbid"); the UI does too, so a
    # typo in the page script fails loudly instead of being ignored.
    response = patch_review(ui, "job-awaiting-1", {"expected_version": 3, "provaa": "x"})

    assert response.status_code == 400


def test_non_json_requests_are_refused(ui, fake_orchestrator):
    # A form on another website can make the browser POST with the
    # reviewer's saved credentials, but it cannot send application/json.
    # Refusing anything else blocks such cross-site requests.
    response = ui.patch(
        REVIEW_URL.format("job-awaiting-1"),
        content="expected_version=3&action=discard",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )

    assert response.status_code == 415
    assert fake_orchestrator.requests_to("PATCH", "/jobs/job-awaiting-1/review") == []
    assert fake_orchestrator.jobs["job-awaiting-1"].state == JobState.AWAITING_REVIEW


def test_discard_sends_only_the_version_and_action(ui, fake_orchestrator):
    response = patch_review(ui, "job-failed-1", {"expected_version": 3, "action": "discard"})

    assert response.status_code == 200
    assert fake_orchestrator.jobs["job-failed-1"].state == JobState.DISCARDED
    assert response.json()["message"] == "Discarded."


def test_retry_sends_a_failed_job_back_to_ready_and_clears_the_failure(ui, fake_orchestrator):
    response = patch_review(ui, "job-failed-1", {"expected_version": 3, "action": "retry"})

    assert response.status_code == 200
    job = fake_orchestrator.jobs["job-failed-1"]
    assert job.state == JobState.READY
    assert job.failure is None


def test_reprocess_with_a_new_cut(ui, fake_orchestrator):
    body = {"expected_version": 3, "action": "reprocess", "cut_out_s": 18.0}

    response = patch_review(ui, "job-done-1", body)

    assert response.status_code == 200
    job = fake_orchestrator.jobs["job-done-1"]
    assert job.state == JobState.READY
    assert job.cut_out_s == 18.0


def test_review_of_unknown_job_is_a_json_404(ui):
    response = patch_review(ui, "nope", {"expected_version": 1, "action": "save"})

    # The page script gets JSON (not an HTML page) for every API error.
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_request_proxy_relays_to_regenerate_proxy(ui, fake_orchestrator):
    response = ui.post("/review/api/jobs/job-awaiting-1/regenerate-proxy", json={})

    assert response.status_code == 202
    assert len(fake_orchestrator.requests_to("POST", "/jobs/job-awaiting-1/regenerate-proxy")) == 1
    # The orchestrator's waiting reason comes back for the page to show.
    assert response.json()["waiting_reason"] == "no holder is online"
