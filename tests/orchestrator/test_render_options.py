"""Vertical videos, "Girar", "Enquadramento", "Sem áudio" and render features (LIVESTACK-50).

The story: a video filmed upright rendered as a narrow 608×1080 strip, and
some clips had to go out without their sound. Now:

* the orchestrator reads the picture's shape from the probe report the
  first holder already sends, and says on the job whether it is vertical;
* the reviewer's render options are saved with the review and handed to
  the worker in the process task;
* a worker version declares what it can render (render features), each
  process task lists what it needs, and a render is offered only to a
  worker that has it all, because an older worker would ignore the new
  task fields and render the old way;
* the options are render inputs: changing one on a delivered clip marks
  it out of date (test_render_options_inputs.py).

Everything goes through the HTTP API, like the workers and the review UI.
"""

import pytest

from orchestrator.background.sweepers import expire_leases_once
from orchestrator.domain.first_start import DISPLAY_SIZE_BACKFILL, backfill_display_size
from orchestrator.domain.render_features import NO_HOLDER_CAN_RENDER
from orchestrator.testing.scenarios import (
    ask_next,
    claim,
    probe_and_make_proxy,
    receive_job,
    review_to_ready,
    save_review_fields,
)
from shared.models import TaskKind, WorkerRole

# What a 0.4.0 worker declares (worker/render_features.py).
NEW_FEATURES = ["framing", "no_audio"]

# ffprobe's answer for the three kinds of file the brief names (trimmed).
PORTRAIT_PHONE_REPORT = {
    "format": {"duration": "12.0"},
    "streams": [
        {
            "codec_type": "video",
            "width": 1920,
            "height": 1080,
            "avg_frame_rate": "30/1",
            "side_data_list": [{"side_data_type": "Display Matrix", "rotation": -90}],
        },
        {"codec_type": "audio"},
    ],
}
STORED_PORTRAIT_REPORT = {
    "format": {"duration": "12.0"},
    "streams": [{"codec_type": "video", "width": 1080, "height": 1920}, {"codec_type": "audio"}],
}
LANDSCAPE_REPORT = {
    "format": {"duration": "12.0"},
    "streams": [{"codec_type": "video", "width": 1920, "height": 1080}, {"codec_type": "audio"}],
}


def enroll_master(harness, features=None):
    """The master, beating with `features` (None: like a 0.3.0 worker, none at all)."""
    master = harness.enroll(WorkerRole.MASTER, "master", heartbeat=False)
    harness.heartbeat(master, tailnet_address="100.70.0.1", render_features=features)
    return master


def probed_job(harness, master, report):
    """A job whose first holder is `master` and whose probe report is `report`."""
    job_id = receive_job(harness)
    probe_and_make_proxy(harness, master, job_id, report)
    return job_id


def edit(harness, job_id, action="save", **fields):
    job = harness.job(job_id)
    response = harness.client.patch(
        f"/jobs/{job_id}/review",
        json={"expected_version": job["version"], "action": action, **fields},
        auth=harness.reviewer_auth,
    )
    assert response.status_code == 200, response.text
    return response.json()


# --- Knowing the orientation ---


@pytest.mark.parametrize(
    ("report", "size", "vertical"),
    [
        (PORTRAIT_PHONE_REPORT, (1080, 1920), True),
        (STORED_PORTRAIT_REPORT, (1080, 1920), True),
        (LANDSCAPE_REPORT, (1920, 1080), False),
    ],
    ids=["portrait-phone-side-data", "stored-portrait", "landscape"],
)
def test_the_probe_report_says_whether_the_video_is_vertical(harness, report, size, vertical):
    # Setup: a master sends the probe report as first holder.
    master = enroll_master(harness, NEW_FEATURES)
    job_id = probed_job(harness, master, report)

    # The job keeps the size players show (after the rotation note) and
    # whether that is taller than wide...
    job = harness.job(job_id)
    assert (job["display_width"], job["display_height"]) == size
    assert job["vertical"] is vertical
    # ...and the review queue's row carries the same flag for its marker.
    queue = harness.client.get("/review/queue", auth=harness.reviewer_auth).json()["items"]
    assert [item["vertical"] for item in queue if item["id"] == job_id] == [vertical]


def test_a_job_without_a_probe_report_has_no_orientation_yet(harness):
    job_id = receive_job(harness)
    job = harness.job(job_id)
    assert job["vertical"] is None
    assert job["display_width"] is None


def test_older_jobs_get_their_size_from_the_stored_report_once_at_startup(harness):
    # Setup: a job probed before LIVESTACK-50 has its report but no size.
    # Simulate that by clearing the size the report just set, and by
    # forgetting that this orchestrator's own start already ran the
    # backfill (on an empty database, when the harness was built).
    master = enroll_master(harness, NEW_FEATURES)
    job_id = probed_job(harness, master, PORTRAIT_PHONE_REPORT)
    with harness.context.database.transaction() as connection:
        connection.execute(
            "UPDATE jobs SET display_width = NULL, display_height = NULL WHERE id = ?", (job_id,)
        )
        connection.execute(
            "DELETE FROM first_start_imports WHERE name = ?", (DISPLAY_SIZE_BACKFILL,)
        )

    # What it does: the first-start backfill, twice (a second start).
    first = backfill_display_size(harness.context.database, harness.clock.now())
    second = backfill_display_size(harness.context.database, harness.clock.now())

    # The size came back from the stored report, and only the first time
    # did anything (it is marked done, like the recording-time backfill).
    assert (first, second) == (1, 0)
    assert harness.job(job_id)["vertical"] is True


# --- The reviewer's options ---


def test_the_options_are_saved_with_the_review_and_girar_turns_the_orientation(harness):
    master = enroll_master(harness, NEW_FEATURES)
    job_id = probed_job(harness, master, PORTRAIT_PHONE_REPORT)
    # The defaults: no turn, blurred sides, band in the middle, sound kept.
    job = harness.job(job_id)
    assert (job["rotation"], job["framing"], job["crop_position"], job["no_audio"]) == (
        0,
        "blurred",
        0.5,
        False,
    )

    saved = edit(harness, job_id, rotation=90, framing="crop", crop_position=0.3, no_audio=True)

    assert (saved["rotation"], saved["framing"], saved["crop_position"], saved["no_audio"]) == (
        90,
        "crop",
        0.3,
        True,
    )
    # A quarter turn makes the upright phone video horizontal: the
    # orientation follows "Girar".
    assert saved["vertical"] is False


def test_options_that_are_not_allowed_are_refused(harness):
    master = enroll_master(harness, NEW_FEATURES)
    job_id = probed_job(harness, master, LANDSCAPE_REPORT)
    version = harness.job(job_id)["version"]
    for bad in ({"rotation": 45}, {"framing": "stretch"}, {"crop_position": 1.5}):
        response = harness.client.patch(
            f"/jobs/{job_id}/review",
            json={"expected_version": version, "action": "save", **bad},
            auth=harness.reviewer_auth,
        )
        assert response.status_code == 400, bad


def test_the_process_task_carries_the_options_and_the_features_it_needs(harness):
    master = enroll_master(harness, NEW_FEATURES)
    job_id = probed_job(harness, master, PORTRAIT_PHONE_REPORT)
    edit(harness, job_id, framing="black_bars", no_audio=True)
    review_to_ready(harness, job_id)

    task = ask_next(harness, master, [TaskKind.PROCESS])

    assert task["job_id"] == job_id
    assert (task["rotation"], task["framing"], task["no_audio"]) == (0, "black_bars", True)
    # Upright → framing; "Sem áudio" on a raw with sound → no_audio.
    assert task["required_render_features"] == ["framing", "no_audio"]


# --- Which workers may render ---


def test_a_job_that_needs_a_feature_is_not_offered_to_a_worker_without_it(harness):
    # Setup: the master runs an old version (declares nothing) and holds an
    # upright video that is ready.
    master = enroll_master(harness, features=None)
    job_id = probed_job(harness, master, PORTRAIT_PHONE_REPORT)
    review_to_ready(harness, job_id)

    # No offer, and claiming anyway is refused: an old worker would render
    # the narrow strip of the bug report.
    assert ask_next(harness, master, [TaskKind.PROCESS]) is None
    refused = claim(harness, master, job_id, TaskKind.PROCESS)
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "not_claimable"
    assert "framing" in refused.json()["error"]["message"]

    # The job says what it waits for (the sweeper writes it).
    expire_leases_once(harness.context)
    assert harness.job(job_id)["waiting_reason"] == NO_HOLDER_CAN_RENDER

    # The same PC updated to 0.4.0: its next beat declares the features
    # (and, as every beat does, the raws it holds), and the render is
    # offered; the waiting sentence goes away.
    harness.heartbeat(master, render_features=NEW_FEATURES, held_job_ids=[job_id])
    assert ask_next(harness, master, [TaskKind.PROCESS])["job_id"] == job_id
    expire_leases_once(harness.context)
    assert harness.job(job_id)["waiting_reason"] is None


def test_sem_audio_needs_its_own_feature(harness):
    # A worker that can frame but not drop the sound (as a future version
    # might declare a subset) does not get a landscape job with "Sem áudio".
    master = enroll_master(harness, features=["framing"])
    job_id = probed_job(harness, master, LANDSCAPE_REPORT)
    edit(harness, job_id, no_audio=True)
    review_to_ready(harness, job_id)
    assert ask_next(harness, master, [TaskKind.PROCESS]) is None
    harness.heartbeat(master, render_features=["framing", "no_audio"], held_job_ids=[job_id])
    assert ask_next(harness, master, [TaskKind.PROCESS])["job_id"] == job_id


def test_a_job_that_needs_nothing_new_goes_to_any_worker_as_before(harness):
    # A landscape 16:9 video with sound: an old worker renders it exactly
    # as a new one would, so it is offered to the old one.
    master = enroll_master(harness, features=None)
    job_id = probed_job(harness, master, LANDSCAPE_REPORT)
    # Even "barras pretas" changes nothing on a 16:9 picture.
    edit(harness, job_id, framing="black_bars")
    review_to_ready(harness, job_id)

    task = ask_next(harness, master, [TaskKind.PROCESS])

    assert task["job_id"] == job_id
    assert task["required_render_features"] == []


def test_a_0_3_0_heartbeat_is_still_accepted_and_means_no_features(harness):
    # Setup: a worker that once declared features (0.4.0)...
    master = enroll_master(harness, NEW_FEATURES)
    fleet = harness.client.get("/fleet", auth=harness.reviewer_auth).json()
    assert fleet["workers"][0]["worker"]["capabilities"]["render_features"] == NEW_FEATURES

    # ...is reinstalled with 0.3.0, whose beats have no render_features.
    answer = harness.heartbeat(master)

    # The beat is accepted as always, and the worker now counts as one that
    # can render nothing new.
    assert answer["accepting_new_raws"] is True
    fleet = harness.client.get("/fleet", auth=harness.reviewer_auth).json()
    assert fleet["workers"][0]["worker"]["capabilities"]["render_features"] == []


def test_enrollment_records_the_features_a_new_worker_declares(harness):
    created = harness.client.post(
        "/admin/enrollment-tokens",
        json={"volunteer_name": "pc", "role": "worker"},
        headers=harness.admin_headers,
    ).json()
    enrolled = harness.client.post(
        "/enroll",
        json={
            "enrollment_token": created["token"],
            "worker_name": "pc",
            "capabilities": {
                "has_nvenc": False,
                "video_encoder": "libx264",
                "render_features": NEW_FEATURES,
            },
            "storage_cap_bytes": 10**11,
        },
    )
    assert enrolled.status_code == 201, enrolled.text
    fleet = harness.client.get("/fleet", auth=harness.reviewer_auth).json()
    assert fleet["workers"][0]["worker"]["capabilities"]["render_features"] == NEW_FEATURES


def test_save_review_fields_helper_still_works_with_the_options(harness):
    # The shared scenario helper sends only what it is given; the options
    # keep their values (left out = keep), like every other field.
    master = enroll_master(harness, NEW_FEATURES)
    job_id = probed_job(harness, master, PORTRAIT_PHONE_REPORT)
    edit(harness, job_id, framing="crop")
    save_review_fields(harness, job_id, car_number=7)
    assert harness.job(job_id)["framing"] == "crop"
