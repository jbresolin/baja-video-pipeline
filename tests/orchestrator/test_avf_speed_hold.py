""" "Esperar o resultado oficial" waits for the speed too (LIVESTACK-67).

What this module is for: "Pronto" with "Esperar o resultado oficial"
ticked holds the render until what it would draw from the results is
fetched (LIVESTACK-62). For "Aceleração e Velocidade" that is the clock's
official time (table ACE) and, when a speed would be drawn under it, the
run's official speed (table VEL). The two tables are published
separately, so a render must not start with the clock but no speed.

What these tests prove: with the speed mark set, the hold stays while
only the clock's time is in and ends when the speed arrives (the event
lists "speed" among what it waited for); without a speed mark the speed
is not waited for; when everything is already in there is no hold.

What they do not cover: the deadline, "Renderizar agora" and the other
ways a hold ends, which work the same for every prova (test_result_hold.py).
"""

from orchestrator.domain.pass_results_fetch import poll_due_tables
from tests.orchestrator.avf_data import (
    CLOCK_TABLE,
    SPEED_TABLE,
    avf_job_in_review,
    set_run_value,
    set_up_avf,
)
from tests.orchestrator.result_hold_helpers import click_pronto, event_kinds, events_of_kind
from tests.orchestrator.test_pass_results_render import enroll_master

FEATURES_0_8_0 = ["framing", "no_audio", "pass_result", "clock", "clock_top_right", "speed"]
LARGADA_S = 1.0
SPEED_MARK_S = 4.0


def avf_job_before_the_speed_is_published(harness) -> str:
    """Car 1's first AVF run in review: its ACE time is in, its VEL speed is still "-"."""
    set_up_avf(harness)
    set_run_value(harness.results_api.tables[SPEED_TABLE], car_number=1, run=1, value="-")
    master = enroll_master(harness, FEATURES_0_8_0)
    job_id = avf_job_in_review(harness, car=1, passagem=1, worker=master)
    poll_due_tables(harness.context)
    return job_id


def judges_publish_the_speed(harness) -> None:
    """Car 1's first run gets its 37.18 km/h; the next fetch, 30 s later, sees it."""
    set_run_value(harness.results_api.tables[SPEED_TABLE], car_number=1, run=1, value=37.18)
    harness.clock.advance(30)
    poll_due_tables(harness.context)


def test_with_a_speed_mark_the_render_waits_for_the_speed(harness):
    # Arrange: the clock's official time is in, the speed is not.
    job_id = avf_job_before_the_speed_is_published(harness)

    # Act: "Pronto" with the box ticked, both marks set.
    click_pronto(harness, job_id, largada_s=LARGADA_S, speed_mark_s=SPEED_MARK_S)

    # Assert: held, and the event says it waits for the clock and the speed.
    job = harness.job(job_id)
    assert job["result_hold_until"] is not None, "the render must wait for the speed"
    [started] = events_of_kind(harness, job_id, "result_hold_started")
    waits_for = started["details"]["waits_for"]
    assert waits_for == ["clock", "speed"], "the event must name the clock and the speed"


def test_the_speed_arriving_releases_the_render(harness):
    # Arrange: a render held for car 1's speed.
    job_id = avf_job_before_the_speed_is_published(harness)
    click_pronto(harness, job_id, largada_s=LARGADA_S, speed_mark_s=SPEED_MARK_S)

    # Act: the judges publish the speed; the next fetch sees it.
    judges_publish_the_speed(harness)

    # Assert: the hold is over and the render would draw the speed.
    job = harness.job(job_id)
    assert job["result_hold_until"] is None, "the speed arriving must release the render"
    assert "result_hold_released_result_arrived" in event_kinds(harness, job_id)
    assert job["pass_speed"]["render"]["text"] == "37,2", "and the clip gets the speed"


def test_a_new_clock_table_answer_alone_does_not_release_it(harness):
    # Arrange: a render held for car 1's speed.
    job_id = avf_job_before_the_speed_is_published(harness)
    click_pronto(harness, job_id, largada_s=LARGADA_S, speed_mark_s=SPEED_MARK_S)

    # Act: 30 s later both tables are fetched again; ACE has the time, VEL still "-".
    harness.clock.advance(30)
    fetched = poll_due_tables(harness.context)

    # Assert: both were fetched, and the render still waits.
    assert sorted(fetched) == [CLOCK_TABLE, SPEED_TABLE], "both tables were asked for"
    assert harness.job(job_id)["result_hold_until"] is not None, "still waiting for the speed"


def test_without_a_speed_mark_the_speed_is_not_waited_for(harness):
    # Arrange: the clock's official time is in, the speed is not.
    job_id = avf_job_before_the_speed_is_published(harness)

    # Act: "Pronto" with the box ticked and only the largada marked.
    click_pronto(harness, job_id, largada_s=LARGADA_S)

    # Assert: no hold: the clock is in and no speed would be drawn.
    assert harness.job(job_id)["result_hold_until"] is None, "nothing left to wait for"


def test_with_the_speed_already_in_there_is_no_hold(harness):
    # Arrange: both tables have car 1's first run.
    set_up_avf(harness)
    master = enroll_master(harness, FEATURES_0_8_0)
    job_id = avf_job_in_review(harness, car=1, passagem=1, worker=master)
    poll_due_tables(harness.context)

    # Act: "Pronto" with the box ticked and both marks.
    click_pronto(harness, job_id, largada_s=LARGADA_S, speed_mark_s=SPEED_MARK_S)

    # Assert: rendered at once: waiting could not change the clip.
    assert harness.job(job_id)["result_hold_until"] is None, "everything is in: no hold"
