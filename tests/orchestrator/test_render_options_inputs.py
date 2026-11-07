"""Render options are render inputs: changing one on a delivered clip (LIVESTACK-50).

Background: every clip version remembers what it was rendered from
(LIVESTACK-43, test_render_inputs.py), and the job says when the clip on
air no longer matches the review. The render options ("Girar",
"Enquadramento" with the crop band, "Sem áudio") are inputs too, compared
*as they show*: framing on a 16:9 picture changes nothing, so it never
marks a clip out of date.

The fleet and the jobs are built with the helpers of test_render_options.py.
"""

import hashlib

from orchestrator.domain.render_features import NO_HOLDER_CAN_RENDER
from orchestrator.testing.scenarios import SAMPLE_CLIP, claim_ok, review_to_ready
from shared.models import TaskKind, WorkerRole
from tests.orchestrator.test_render_inputs import render_and_push, report_inputs
from tests.orchestrator.test_render_options import (
    LANDSCAPE_REPORT,
    NEW_FEATURES,
    PORTRAIT_PHONE_REPORT,
    edit,
    enroll_master,
    probed_job,
)


def test_changing_the_framing_of_a_delivered_clip_marks_it_out_of_date(harness):
    # Setup: an upright video, rendered by a 0.4.0 worker that reports its
    # options (blurred, no turn, sound kept), delivered to the studio.
    harness.client.patch("/admin/settings", json={"event_id": "26BR"}, auth=harness.admin_auth)
    master = enroll_master(harness, NEW_FEATURES)
    studio = harness.enroll(WorkerRole.STUDIO, "studio", tailnet_address="100.70.0.3")
    job_id = probed_job(harness, master, PORTRAIT_PHONE_REPORT)
    review_to_ready(harness, job_id, car_number=1, passagem=3)
    deliver_with_options_reported(harness, master, studio, job_id)
    assert harness.job(job_id)["clip_inputs"]["changed"] == []

    # What it does: the reviewer picks "barras pretas" and saves.
    edit(harness, job_id, framing="black_bars")

    # The clip on air no longer matches: framing is listed, the queue flag
    # is on, and the clip versions keep what was rendered.
    job = harness.job(job_id)
    assert job["clip_inputs"]["changed"] == ["framing"]
    assert job["clip_inputs"]["clip"]["framing"] == "blurred"
    assert job["clip_inputs"]["now"]["framing"] == "black_bars"
    outdated = harness.client.get(
        "/review/queue", params={"state": "done", "outdated": "true"}, auth=harness.reviewer_auth
    ).json()["items"]
    assert [item["id"] for item in outdated] == [job_id]


def test_girar_sem_audio_and_the_crop_band_are_render_inputs_too(harness):
    harness.client.patch("/admin/settings", json={"event_id": "26BR"}, auth=harness.admin_auth)
    master = enroll_master(harness, NEW_FEATURES)
    studio = harness.enroll(WorkerRole.STUDIO, "studio", tailnet_address="100.70.0.3")
    job_id = probed_job(harness, master, PORTRAIT_PHONE_REPORT)
    edit(harness, job_id, framing="crop", crop_position=0.2)
    review_to_ready(harness, job_id, car_number=1, passagem=3)
    deliver_with_options_reported(harness, master, studio, job_id)

    # Moving the band is a framing change; a hair's breadth is not.
    edit(harness, job_id, crop_position=0.203)
    assert harness.job(job_id)["clip_inputs"]["changed"] == []
    edit(harness, job_id, crop_position=0.6)
    assert harness.job(job_id)["clip_inputs"]["changed"] == ["framing"]
    edit(harness, job_id, crop_position=0.2, rotation=180, no_audio=True)
    assert harness.job(job_id)["clip_inputs"]["changed"] == ["rotation", "audio"]


def test_framing_on_a_16_9_clip_never_marks_it_out_of_date(harness):
    harness.client.patch("/admin/settings", json={"event_id": "26BR"}, auth=harness.admin_auth)
    master = enroll_master(harness, NEW_FEATURES)
    studio = harness.enroll(WorkerRole.STUDIO, "studio", tailnet_address="100.70.0.3")
    job_id = probed_job(harness, master, LANDSCAPE_REPORT)
    review_to_ready(harness, job_id, car_number=1, passagem=3)
    deliver_with_options_reported(harness, master, studio, job_id)

    # "Barras pretas" changes nothing on a 16:9 picture: nothing to redo.
    edit(harness, job_id, framing="black_bars")

    assert harness.job(job_id)["clip_inputs"]["changed"] == []


def test_a_vertical_clip_rendered_before_the_options_shows_as_out_of_date(harness):
    # A clip of an upright video rendered by a 0.3.0 worker (it reports no
    # options) is the narrow strip: the review now asks for blurred sides,
    # so the clip is honestly out of date and a reprocess fixes it.
    harness.client.patch("/admin/settings", json={"event_id": "26BR"}, auth=harness.admin_auth)
    master = enroll_master(harness, NEW_FEATURES)
    studio = harness.enroll(WorkerRole.STUDIO, "studio", tailnet_address="100.70.0.3")
    job_id = probed_job(harness, master, PORTRAIT_PHONE_REPORT)
    review_to_ready(harness, job_id, car_number=1, passagem=3)
    render_and_push(harness, master, studio, job_id)  # a report without options

    job = harness.job(job_id)
    assert job["clip_inputs"]["clip"]["framing"] is None
    assert job["clip_inputs"]["changed"] == ["framing"]


def test_a_reprocess_waits_and_says_so_when_no_holder_can_render_the_new_options(harness):
    harness.client.patch("/admin/settings", json={"event_id": "26BR"}, auth=harness.admin_auth)
    master = enroll_master(harness, features=None)  # 0.3.0
    studio = harness.enroll(WorkerRole.STUDIO, "studio", tailnet_address="100.70.0.3")
    job_id = probed_job(harness, master, LANDSCAPE_REPORT)
    review_to_ready(harness, job_id, car_number=1, passagem=3)
    render_and_push(harness, master, studio, job_id)

    # "Salvar e reprocessar" with "Sem áudio": only a 0.4.0 worker can do it.
    job = edit(harness, job_id, action="reprocess", no_audio=True)

    assert job["state"] == "ready"
    assert job["waiting_reason"] == NO_HOLDER_CAN_RENDER


def deliver_with_options_reported(harness, master, studio, job_id):
    """Like render_and_push, but the worker reports the options it used (0.4.0)."""
    lease_id, task = claim_ok(harness, master, job_id, TaskKind.PROCESS)
    sha256 = hashlib.sha256(SAMPLE_CLIP).hexdigest()
    options = {key: task[key] for key in ("rotation", "framing", "crop_position", "no_audio")}
    response = report_inputs(harness, master, job_id, lease_id, task, sha256, **options)
    assert response.status_code == 204, response.text
    ack = harness.client.post(
        f"/jobs/{job_id}/delivered",
        json={
            "sha256": sha256,
            "size_bytes": len(SAMPLE_CLIP),
            "relative_path": task["clip_relative_path"],
        },
        headers=studio.headers,
    )
    assert ack.status_code == 200, ack.text
