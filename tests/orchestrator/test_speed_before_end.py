"""The top speed shown N seconds before the clip's end: job, render task, preview, "Testar".

What this module is for (LIVESTACK-68): "Aceleração e Velocidade" shows
the run's top speed under the clock. With `"seconds_before_end": 3` in the
prova's speed block it is shown for the last 3 seconds of the cut clip,
and the reviewer marks nothing. The orchestrator works out where that
starts, in raw-video seconds:

    start = (the cut-out, or the video's duration without one) − 3 s,
            rounded down to the millisecond, never before the cut-in

and puts it where the speed mark would go in the render task
(RenderSpeed.speed_mark_s), so a 0.8.0 worker draws it with the rule it
already has. A speed mark saved earlier is kept but not used, so switching
the configuration back brings it back.

What these tests prove, through the HTTP API of a real orchestrator (the
test harness) with the AVF fixtures (car 1's first run: 4.354 s and
37.18 km/h): the start from the cut-out, from the duration, clamped to the
cut-in, and none without an end; that the start follows the cut-out; that
the stored mark is ignored, not cleared; the render task with the computed
start and the "speed" feature; and "Testar" naming the mode.

What they do not cover: the result hold and the "clip no longer matches"
check (test_speed_before_end_hold_and_inputs.py), the rule's arithmetic
(tests/shared/test_speed_before_end_rule.py) and the job page.
"""

from orchestrator.domain.pass_results_fetch import poll_due_tables
from orchestrator.domain.pass_speed import pass_speed_for_job
from orchestrator.repositories import jobs as jobs_repo
from orchestrator.testing.scenarios import claim_ok
from shared.models import TaskKind
from tests.orchestrator.avf_data import AVF_CONFIG_TEXT, AVF_NAME, avf_job_in_review, set_up_avf
from tests.orchestrator.speed_before_end_data import BEFORE_END_CONFIG_TEXT, save_config
from tests.orchestrator.test_avf_speed import FEATURES_0_8_0, new_video
from tests.orchestrator.test_avf_speed_testar import click_testar
from tests.orchestrator.test_pass_results_render import enroll_master
from tests.orchestrator.test_render_inputs import edit_review

LARGADA_S = 1.0


def avf_job_shown_before_end(harness, *, worker=None) -> str:
    """Car 1's first AVF run in review, AVF set to "3 s before the end", both tables fetched.

    No cuts are saved yet ("Salvar" leaves empty cuts empty), so the clip
    is the whole 12 s test video.
    """
    set_up_avf(harness, config_text=BEFORE_END_CONFIG_TEXT)
    job_id = avf_job_in_review(harness, car=1, passagem=1, worker=worker)
    poll_due_tables(harness.context)
    return job_id


# --- Where the speed starts ---


def test_the_speed_starts_3_s_before_the_cut_out_with_no_mark(harness):
    # Arrange: car 1's AVF job, the prova set to 3 s before the end.
    job_id = avf_job_shown_before_end(harness)

    # Act: the reviewer cuts 0.5–5.0 s, marks the largada (for the clock), and saves.
    edit_review(harness, job_id, cut_in_s=0.5, cut_out_s=5.0, largada_s=LARGADA_S)

    # Assert: the job page learns the mode and the start, and a render
    # would draw 37,2 km/h from 2.0 s (5.0 − 3) without any speed mark.
    speed = harness.job(job_id)["pass_speed"]
    assert speed["seconds_before_end"] == 3, "the job page must know the mode"
    assert speed["speed_start_s"] == 2.0, "the start is the cut-out minus 3 s"
    assert speed["speed_mark_s"] is None, "nothing was marked"
    expected = {"speed_mark_s": 2.0, "text": "37,2", "unit": "km/h"}
    assert speed["render"] == expected, "the render draws the speed from the computed start"


def test_moving_the_cut_out_moves_the_start(harness):
    # Arrange: car 1's job cut at 5.0 s, so the speed starts at 2.0 s.
    job_id = avf_job_shown_before_end(harness)
    edit_review(harness, job_id, cut_in_s=0.5, cut_out_s=5.0, largada_s=LARGADA_S)

    # Act: the reviewer moves the cut-out to 6.25 s.
    edit_review(harness, job_id, cut_out_s=6.25)

    # Assert: the start follows it, still 3 s before the end.
    speed = harness.job(job_id)["pass_speed"]
    assert speed["speed_start_s"] == 3.25, "the start must follow the cut-out"
    assert speed["render"]["speed_mark_s"] == 3.25, "and so must the render"


def test_a_clip_shorter_than_n_shows_the_speed_from_the_cut_in(harness):
    # Arrange: car 1's AVF job, the prova set to 3 s before the end.
    job_id = avf_job_shown_before_end(harness)

    # Act: the reviewer keeps only 1.5 s of video, 3.5–5.0 s.
    edit_review(harness, job_id, cut_in_s=3.5, cut_out_s=5.0, largada_s=LARGADA_S)

    # Assert: 5.0 − 3 = 2.0 s is before the clip starts: the speed shows throughout.
    speed = harness.job(job_id)["pass_speed"]
    assert speed["speed_start_s"] == 3.5, "never earlier than the cut-in"
    assert speed["render"]["speed_mark_s"] == 3.5, "so the whole clip shows the speed"


def test_a_saved_job_without_a_cut_out_ends_with_the_video(harness):
    # Arrange: car 1's AVF job, no cuts saved, in a 12 s video.
    job_id = avf_job_shown_before_end(harness)

    # Act: the reviewer marks the largada only, and saves.
    edit_review(harness, job_id, largada_s=LARGADA_S)

    # Assert: no cut-out, so the clip ends with the video: 12 − 3 = 9 s.
    job = harness.job(job_id)
    assert job["cut_out_s"] is None, "Salvar leaves an empty cut-out empty"
    assert job["pass_speed"]["speed_start_s"] == 9.0, "the video's end minus 3 s"
    assert job["pass_speed"]["render"]["speed_mark_s"] == 9.0, "and the render draws it there"


def test_on_a_new_video_the_live_block_uses_the_end_of_the_video(harness):
    # Arrange: AVF set to 3 s before the end; a new 12 s video with nothing
    # saved, so it has no cut-out yet.
    set_up_avf(harness, config_text=BEFORE_END_CONFIG_TEXT)
    job_id = new_video(harness)
    assert harness.job(job_id)["cut_out_s"] is None, "no cut-out saved"

    # Act: the form says AVF, car 1, passagem 1 (the live blocks ask, nothing saved).
    response = harness.client.get(
        f"/jobs/{job_id}/pass-preview",
        params={"prova": AVF_NAME, "car_number": 1, "passagem": 1},
        auth=harness.reviewer_auth,
    )

    # Assert: the live block brings the mode, and the start is 12 − 3 = 9 s.
    assert response.status_code == 200, response.text
    speed = response.json()["pass_speed"]
    assert speed["seconds_before_end"] == 3, "the live block must bring the mode"
    assert speed["speed_start_s"] == 9.0, "the video's end minus 3 s"


def test_with_no_cut_out_and_no_duration_there_is_no_speed(harness):
    # Arrange: car 1's job with a largada; then, as the job is read, take
    # away its cut-out and its duration (a raw no holder has probed yet).
    job_id = avf_job_shown_before_end(harness)
    edit_review(harness, job_id, largada_s=LARGADA_S)
    with harness.context.database.transaction() as connection:
        columns = dict(jobs_repo.get_job_row(connection, job_id))
        columns.update({"cut_out_s": None, "duration_s": None})

        # Act: work out the job's speed from those columns.
        speed = pass_speed_for_job(connection, columns)

    # Assert: the speed is known, but with no end there is no start, so no pill.
    assert speed.official_text == "37,2", "the official speed is still known"
    assert speed.speed_start_s is None, "no end of the clip, no start"
    assert speed.render is None, "and nothing drawn"


# --- The stored mark: ignored, not cleared ---


def test_a_stored_speed_mark_is_kept_but_not_used_and_comes_back(harness):
    # Arrange: AVF from the mark (as in LIVESTACK-67); the reviewer marked
    # the trap at 4.0 s in a clip cut 0.5–5.0 s.
    set_up_avf(harness, config_text=AVF_CONFIG_TEXT)
    job_id = avf_job_in_review(harness, car=1, passagem=1)
    poll_due_tables(harness.context)
    edit_review(harness, job_id, cut_in_s=0.5, cut_out_s=5.0, largada_s=LARGADA_S, speed_mark_s=4.0)

    # Act: the admin switches AVF to "3 s before the end", then back.
    save_config(harness, BEFORE_END_CONFIG_TEXT)
    in_end_mode = harness.job(job_id)
    save_config(harness, AVF_CONFIG_TEXT)
    back_in_mark_mode = harness.job(job_id)

    # Assert: in end mode the mark is still stored but the start is 2.0 s;
    # back in mark mode the speed starts at the mark again.
    assert in_end_mode["speed_mark_s"] == 4.0, "the mark must not be cleared"
    assert in_end_mode["pass_speed"]["render"]["speed_mark_s"] == 2.0, "nor used"
    assert back_in_mark_mode["pass_speed"]["seconds_before_end"] is None, "the mark mode again"
    assert back_in_mark_mode["pass_speed"]["render"]["speed_mark_s"] == 4.0, "from the mark"


def test_in_mark_mode_the_start_is_the_mark(harness):
    # Arrange: AVF from the mark, car 1's job marked at 4.0 s.
    set_up_avf(harness, config_text=AVF_CONFIG_TEXT)
    job_id = avf_job_in_review(harness, car=1, passagem=1)
    poll_due_tables(harness.context)

    # Act: the reviewer marks the largada and the trap.
    edit_review(harness, job_id, largada_s=LARGADA_S, speed_mark_s=4.0)

    # Assert: no N, and the start the page reads is the mark itself.
    speed = harness.job(job_id)["pass_speed"]
    assert speed["seconds_before_end"] is None, "no seconds_before_end in the configuration"
    assert speed["speed_start_s"] == 4.0, "the start is the reviewer's mark"


# --- The render task ---


def test_the_render_task_carries_the_computed_start_for_a_0_8_0_worker(harness):
    # Arrange: a 0.8.0 master holds car 1's job, cut 0.5–5.0 s, ready.
    master = enroll_master(harness, FEATURES_0_8_0)
    job_id = avf_job_shown_before_end(harness, worker=master)
    edit_review(harness, job_id, action="ready", cut_in_s=0.5, cut_out_s=5.0, largada_s=LARGADA_S)

    # Act: the master claims the render.
    _, task = claim_ok(harness, master, job_id, TaskKind.PROCESS)

    # Assert: the speed as a 0.8.0 worker already draws it, from 2.0 s,
    # under the clock, needing nothing newer than the "speed" feature.
    expected = {"speed_mark_s": 2.0, "text": "37,2", "unit": "km/h"}
    assert task["pass_speed"] == expected, "the start goes where the mark would"
    assert task["pass_clock"]["official_units"] == 435, "under the clock stopping at 4,35"
    assert "speed" in task["required_render_features"], "the 0.8.0 feature, nothing newer"
    assert set(task["required_render_features"]) <= set(FEATURES_0_8_0), "no new feature"


# --- "Testar" ---


def test_testar_names_the_mode(harness):
    # Arrange: AVF set to 3 s before the end.
    set_up_avf(harness, config_text=BEFORE_END_CONFIG_TEXT)

    # Act: the admin clicks "Testar" for car 1, passagem 1.
    answer = click_testar(harness, "AVF", 1, 1)

    # Assert: the speed, and how long before the end it comes in.
    assert answer["speed_text"] == "37,2", "the speed as before"
    assert answer["speed_seconds_before_end"] == 3, "and the mode, for the sentence"


def test_testar_in_mark_mode_has_no_seconds_before_end(harness):
    # Arrange: AVF from the mark.
    set_up_avf(harness, config_text=AVF_CONFIG_TEXT)

    # Act: the admin clicks "Testar".
    answer = click_testar(harness, "AVF", 1, 1)

    # Assert: no mode to name.
    assert answer["speed_seconds_before_end"] is None, "the mark mode says nothing more"
