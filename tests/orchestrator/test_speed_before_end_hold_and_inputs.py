"""The speed N seconds before the end: the result hold and the "clip no longer matches" check.

What this module is for (LIVESTACK-68): with `"seconds_before_end": 3` in
"Aceleração e Velocidade"'s speed block, the top speed is shown for the
last 3 s of the clip with no mark. Two things that read the speed must
follow:

* "Esperar o resultado oficial" (LIVESTACK-62) holds a ready render until
  what it would draw is fetched. In this mode the speed is drawn whenever
  the clock is, so it is waited for whenever the clock is: no mark needed.
* A delivered clip remembers what it was rendered from (LIVESTACK-43). The
  speed's start is the cut-out minus 3 s, so moving the cut-out moves it
  (the clip is flagged for the cut and the speed); switching the prova
  between the mark and "3 s before the end" flags a clip when the start
  actually changes, and not when it lands on the same moment.

What these tests prove: those rules, through the HTTP API of a real
orchestrator with the AVF fixtures (car 1's first run: 4.354 s, 37.18 km/h).

What they do not cover: the deadline and the other ways a hold ends (the
same for every prova: test_result_hold.py), and the sentences the job page
writes (tests/review_ui/test_speed_before_end_page.py).
"""

from orchestrator.domain.pass_results_fetch import poll_due_tables
from shared.models import WorkerRole
from tests.orchestrator.avf_data import (
    AVF_CONFIG_TEXT,
    SPEED_TABLE,
    avf_job_in_review,
    set_run_value,
    set_up_avf,
)
from tests.orchestrator.result_hold_helpers import click_pronto, event_kinds, events_of_kind
from tests.orchestrator.speed_before_end_data import BEFORE_END_CONFIG_TEXT, save_config
from tests.orchestrator.test_avf_speed import FEATURES_0_8_0
from tests.orchestrator.test_avf_speed_hold import judges_publish_the_speed
from tests.orchestrator.test_avf_speed_render_inputs import changed_inputs, deliver
from tests.orchestrator.test_pass_results_render import enroll_master
from tests.orchestrator.test_render_inputs import edit_review

LARGADA_S = 1.0

# --- The result hold ---


def end_mode_job_before_the_speed_is_published(harness) -> str:
    """Car 1's first run in review, AVF set to 3 s before the end; ACE has
    the time, VEL still has "-" for that run."""
    set_up_avf(harness, config_text=BEFORE_END_CONFIG_TEXT)
    set_run_value(harness.results_api.tables[SPEED_TABLE], car_number=1, run=1, value="-")
    master = enroll_master(harness, FEATURES_0_8_0)
    job_id = avf_job_in_review(harness, car=1, passagem=1, worker=master)
    poll_due_tables(harness.context)
    return job_id


def test_the_render_waits_for_the_speed_without_any_mark(harness):
    # Arrange: the clock's official time is in, the speed is not.
    job_id = end_mode_job_before_the_speed_is_published(harness)

    # Act: "Pronto" with the box ticked and only the largada marked (no speed mark).
    click_pronto(harness, job_id, largada_s=LARGADA_S)

    # Assert: held, and the event says it waits for the clock and the speed.
    assert harness.job(job_id)["result_hold_until"] is not None, "the render must wait"
    [started] = events_of_kind(harness, job_id, "result_hold_started")
    waits_for = started["details"]["waits_for"]
    assert waits_for == ["clock", "speed"], "the speed is waited for along with the clock"


def test_the_speed_arriving_releases_the_render(harness):
    # Arrange: a render held for car 1's speed, no mark set.
    job_id = end_mode_job_before_the_speed_is_published(harness)
    click_pronto(harness, job_id, largada_s=LARGADA_S)

    # Act: the judges publish the speed; the next fetch sees it.
    judges_publish_the_speed(harness)

    # Assert: released, and the clip gets 37,2 km/h 3 s before its 5.0 s cut-out.
    job = harness.job(job_id)
    assert job["result_hold_until"] is None, "the speed arriving must release the render"
    assert "result_hold_released_result_arrived" in event_kinds(harness, job_id)
    expected = {"speed_mark_s": 2.0, "text": "37,2", "unit": "km/h"}
    assert job["pass_speed"]["render"] == expected, "drawn from 3 s before the end"


def test_without_a_clock_the_speed_is_not_waited_for(harness):
    # Arrange: the speed is not published; the reviewer marks no largada,
    # so no clock would be drawn (and the speed sits under the clock).
    job_id = end_mode_job_before_the_speed_is_published(harness)

    # Act: "Pronto" with the box ticked, no marks at all.
    click_pronto(harness, job_id)

    # Assert: nothing a render would draw comes from the results: no hold.
    assert harness.job(job_id)["result_hold_until"] is None, "no clock, nothing to wait for"


# --- The "clip no longer matches" check ---


def delivered_clip(harness, config_text: str, **marks) -> str:
    """Car 1's first AVF run, cut 0.5–5.0 s, rendered by a 0.8.0 master and delivered."""
    set_up_avf(harness, config_text=config_text)
    master = enroll_master(harness, FEATURES_0_8_0)
    studio = harness.enroll(WorkerRole.STUDIO, "studio", tailnet_address="100.70.0.3")
    job_id = avf_job_in_review(harness, car=1, passagem=1, worker=master)
    poll_due_tables(harness.context)
    edit_review(
        harness,
        job_id,
        action="ready",
        cut_in_s=0.5,
        cut_out_s=5.0,
        largada_s=LARGADA_S,
        **marks,
    )
    deliver(harness, master, studio, job_id)
    return job_id


def test_the_clip_remembers_the_computed_start(harness):
    # Arrange and act: a clip rendered with the speed 3 s before its 5.0 s cut-out.
    job_id = delivered_clip(harness, BEFORE_END_CONFIG_TEXT)

    # Assert: the version's inputs hold the start 2.0 s, and nothing differs now.
    inputs = harness.job(job_id)["clip_inputs"]
    assert inputs["clip"]["pass_speed"]["speed_mark_s"] == 2.0, "the start as drawn"
    assert inputs["changed"] == [], "a clip just made matches the review"


def test_moving_the_cut_out_flags_the_cut_and_the_speed(harness):
    # Arrange: the clip on air, cut at 5.0 s, its speed from 2.0 s.
    job_id = delivered_clip(harness, BEFORE_END_CONFIG_TEXT)

    # Act: the reviewer moves the cut-out to 6.0 s and saves.
    edit_review(harness, job_id, cut_out_s=6.0)

    # Assert: the cut changed, and with it where the speed starts (3.0 s).
    assert changed_inputs(harness, job_id) == ["cut", "speed"], "both must be flagged"
    now = harness.job(job_id)["clip_inputs"]["now"]["pass_speed"]
    assert now["speed_mark_s"] == 3.0, "a reprocess would start the speed at 3.0 s"


def test_switching_from_the_mark_to_the_end_flags_a_clip_whose_start_moves(harness):
    # Arrange: a clip made from the mark at 4.0 s (5.0 − 3 would be 2.0 s).
    job_id = delivered_clip(harness, AVF_CONFIG_TEXT, speed_mark_s=4.0)

    # Act: the admin switches AVF to "3 s before the end".
    save_config(harness, BEFORE_END_CONFIG_TEXT)

    # Assert: the speed would now start elsewhere: only "speed" is flagged.
    assert changed_inputs(harness, job_id) == ["speed"], "a different start must flag the clip"


def test_switching_modes_flags_nothing_when_the_start_is_the_same(harness):
    # Arrange: a clip made from a mark at 2.0 s, which is exactly 3 s before
    # its 5.0 s cut-out.
    job_id = delivered_clip(harness, AVF_CONFIG_TEXT, speed_mark_s=2.0)

    # Act: the admin switches AVF to "3 s before the end".
    save_config(harness, BEFORE_END_CONFIG_TEXT)

    # Assert: the clip would look the same, so it is not out of date.
    assert changed_inputs(harness, job_id) == [], "the same start changes nothing on screen"


def test_switching_back_to_a_mark_that_is_not_set_takes_the_speed_away(harness):
    # Arrange: a clip made "3 s before the end", never marked.
    job_id = delivered_clip(harness, BEFORE_END_CONFIG_TEXT)

    # Act: the admin switches AVF back to the mark.
    save_config(harness, AVF_CONFIG_TEXT)

    # Assert: with no mark a reprocess would draw no speed: flagged.
    assert changed_inputs(harness, job_id) == ["speed"], "the speed would go away"
    assert harness.job(job_id)["clip_inputs"]["now"]["pass_speed"] is None, "none drawn now"
