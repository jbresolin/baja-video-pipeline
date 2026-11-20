"""The top speed under the clock: job page, speed mark, render task (LIVESTACK-67).

What this module is for: "Aceleração e Velocidade" (AVF) shows the
acceleration stopwatch top right, frozen on the official ACE time, and
under it the run's top speed from the VEL table ("37,2 km/h"), from the
moment the reviewer marked the car crossing the speed trap. The
orchestrator:

* fetches both tables, the clock's (26BR_ACE) and the speed's (26BR_VEL);
* reads run n of the speed column for passagem n, as the clock does;
* tells the job page the official speed and what a render would draw
  (Job.pass_speed), and stores the reviewer's speed mark;
* hands the speed to the worker in the process task, but only under a
  clock, and only to a worker that declared it can draw it ("speed",
  installer 0.8.0).

What these tests prove: those points, through the HTTP API of a real
orchestrator (the test harness), with the AVF fixtures (real 2026 numbers:
car 1 ran 4.354 s and 37.18 km/h, then 4.328 s and 36.49 km/h).

What they do not cover: "Testar" (test_avf_speed_testar.py), the "clip no
longer matches" check (test_avf_speed_render_inputs.py), the result hold
(test_avf_speed_hold.py), and the worker's drawing (tests/worker/).
"""

import pytest

from orchestrator.background.sweepers import expire_leases_once
from orchestrator.domain.pass_results_config import parse_config
from orchestrator.domain.pass_results_fetch import poll_due_tables
from orchestrator.domain.pass_speed import official_speed
from orchestrator.domain.render_features import NO_HOLDER_CAN_RENDER_SPEED
from orchestrator.domain.results_table import parse_table
from orchestrator.testing.scenarios import (
    ask_next,
    claim,
    claim_ok,
    hold_raw,
    make_proxy,
    receive_job,
)
from shared.models import TaskKind, WorkerRole
from tests.orchestrator.avf_data import (
    AVF_CONFIG_TEXT,
    AVF_NAME,
    CLOCK_TABLE,
    SPEED_TABLE,
    avf_job_in_review,
    avf_table,
    set_up_avf,
)
from tests.orchestrator.test_pass_results_render import enroll_master
from tests.orchestrator.test_render_inputs import edit_review

FEATURES_0_7_0 = ["framing", "no_audio", "pass_result", "clock", "clock_top_right"]
FEATURES_0_8_0 = [*FEATURES_0_7_0, "speed"]
LARGADA_S = 1.0
SPEED_MARK_S = 4.0
# What a render of car 1's first run draws under the clock.
SPEED_37_2 = {"speed_mark_s": SPEED_MARK_S, "text": "37,2", "unit": "km/h"}


def fetched_avf_job(harness, car=1, passagem=1, *, worker=None) -> str:
    """An AVF job in review with both tables fetched by the background loop."""
    job_id = avf_job_in_review(harness, car, passagem, worker=worker)
    poll_due_tables(harness.context)
    return job_id


# --- The speed text, from the fixtures, run by run ---

# Each row: car, passagem, the text expected (None = no speed), and why the row exists.
SPEED_TEXTS = [
    # Run 1 pairs with run 1: 37.18 km/h with 1 decimal, half up.
    pytest.param(1, 1, "37,2", id="car-1-run-1"),
    # Run 2 pairs with run 2: 36.49 km/h → "36,5".
    pytest.param(1, 2, "36,5", id="car-1-run-2"),
    # A whole number keeps its decimal: 33.2 → "33,2"; and 38.68 → "38,7".
    pytest.param(4, 2, "33,2", id="car-4-run-2"),
    pytest.param(7, 2, "38,7", id="car-7-run-2"),
    # Car 2 did one run only: its second has "-", so no speed at all.
    pytest.param(2, 2, None, id="car-2-run-2-no-speed"),
]


@pytest.mark.parametrize(("car", "passagem", "expected"), SPEED_TEXTS)
def test_the_speed_text_comes_from_the_same_run_of_the_speed_table(car, passagem, expected):
    # Arrange: the configuration and the real speed table.
    avf = parse_config(AVF_CONFIG_TEXT).provas["AVF"]
    table = parse_table(avf_table(SPEED_TABLE))

    # Act: read the car's passagem.
    speed = official_speed(table, avf, car, passagem)

    # Assert: the speed as the pill writes it, or none.
    assert speed.text == expected, f"car {car}, passagem {passagem} must read {expected!r}"


def test_a_run_with_no_speed_says_why():
    # Arrange: the configuration and the real speed table.
    avf = parse_config(AVF_CONFIG_TEXT).provas["AVF"]
    table = parse_table(avf_table(SPEED_TABLE))

    # Act: read car 2's second run, which was never run ("-").
    speed = official_speed(table, avf, 2, 2)

    # Assert: no text, and the reason the job page turns into pt-BR.
    assert speed.text is None, "a '-' run has no speed"
    assert speed.problem == "no_result_yet", "the reason must be 'no result yet'"


# --- Fetching ---


def test_both_tables_of_the_prova_are_fetched(harness):
    # Arrange: AVF configured, a job of it in review.
    set_up_avf(harness)
    avf_job_in_review(harness)

    # Act: one pass of the background loop.
    fetched = poll_due_tables(harness.context)

    # Assert: the clock's table and the speed's table were both asked for.
    assert sorted(fetched) == [CLOCK_TABLE, SPEED_TABLE], "both AVF tables must be fetched"


# --- The job page ---


def test_the_job_page_has_the_official_speed_and_waits_for_the_mark(harness):
    # Arrange: car 1's first run, both tables fetched, nothing marked.
    set_up_avf(harness)
    job_id = fetched_avf_job(harness)

    # Act: read the job as the job page does.
    job = harness.job(job_id)

    # Assert: the speed is configured and known, but not drawn without a mark.
    speed = job["pass_speed"]
    assert speed["configured"] is True, "AVF has a speed block"
    assert (speed["official_text"], speed["unit"], speed["decimals"]) == ("37,2", "km/h", 1)
    assert speed["fetch"]["resultado_id"] == SPEED_TABLE, "its status is the speed table's"
    assert speed["speed_mark_s"] is None, "nothing marked yet"
    assert speed["render"] is None, "no speed pill without a mark"
    assert job["pass_clock"]["official_text"] == "4,35", "the clock freezes on ACE, rounded down"


def test_marking_the_speed_and_the_largada_puts_the_speed_in_the_render(harness):
    # Arrange: car 1's first run, both tables fetched.
    set_up_avf(harness)
    job_id = fetched_avf_job(harness)

    # Act: the reviewer marks the largada and the speed trap, and saves.
    edit_review(harness, job_id, largada_s=LARGADA_S, speed_mark_s=SPEED_MARK_S)

    # Assert: the mark is stored and the render would draw the speed.
    job = harness.job(job_id)
    assert job["speed_mark_s"] == SPEED_MARK_S, "the speed mark must be stored on the job"
    assert job["pass_speed"]["render"] == SPEED_37_2, "the render must draw 37,2 km/h"


def test_without_a_clock_there_is_no_speed_in_the_render(harness):
    # Arrange: car 1's first run, with the speed marked but no largada.
    set_up_avf(harness)
    job_id = fetched_avf_job(harness)

    # Act: save the speed mark alone.
    edit_review(harness, job_id, speed_mark_s=SPEED_MARK_S)

    # Assert: the speed is known and marked, but drawn only under a clock.
    speed = harness.job(job_id)["pass_speed"]
    assert speed["official_text"] == "37,2", "the speed is known"
    assert speed["render"] is None, "no clock (no largada) means no speed pill"


def test_turning_the_clock_off_takes_the_speed_away_too(harness):
    # Arrange: both marks set, so the speed would be drawn.
    set_up_avf(harness)
    job_id = fetched_avf_job(harness)
    edit_review(harness, job_id, largada_s=LARGADA_S, speed_mark_s=SPEED_MARK_S)

    # Act: untick "Mostrar cronômetro".
    edit_review(harness, job_id, show_clock=False)

    # Assert: no clock, so no speed either; the mark itself is kept.
    job = harness.job(job_id)
    assert job["pass_speed"]["render"] is None, "the speed goes with the clock"
    assert job["speed_mark_s"] == SPEED_MARK_S, "the mark stays for when the clock comes back"


def test_the_speed_mark_can_be_removed(harness):
    # Arrange: both marks set; another save without the mark keeps it.
    set_up_avf(harness)
    job_id = fetched_avf_job(harness)
    edit_review(harness, job_id, largada_s=LARGADA_S, speed_mark_s=SPEED_MARK_S)
    edit_review(harness, job_id, prova=AVF_NAME)
    assert harness.job(job_id)["speed_mark_s"] == SPEED_MARK_S, "left out = keep"

    # Act: "Limpar" sends clear_speed_mark.
    edit_review(harness, job_id, clear_speed_mark=True)

    # Assert: no mark, no speed pill; the largada is untouched.
    job = harness.job(job_id)
    assert job["speed_mark_s"] is None, "the mark must be gone"
    assert job["pass_speed"]["render"] is None, "no mark, no speed pill"
    assert job["largada_s"] == LARGADA_S, "clearing the speed must not clear the largada"


def test_a_speed_mark_past_the_end_of_the_video_is_refused(harness):
    # Arrange: an AVF job whose holder probed the raw (its duration is known).
    set_up_avf(harness)
    job_id = fetched_avf_job(harness)
    job = harness.job(job_id)
    assert job["duration_s"] is not None

    # Act: save a speed mark one second after the end.
    response = harness.client.patch(
        f"/jobs/{job_id}/review",
        json={"expected_version": job["version"], "speed_mark_s": job["duration_s"] + 1},
        auth=harness.reviewer_auth,
    )

    # Assert: refused, naming the field.
    assert response.status_code == 422, "a mark after the video's end must be refused"
    assert "speed_mark_s" in response.json()["error"]["message"], "the error must name it"


def test_a_speed_mark_before_the_largada_is_accepted(harness):
    # Arrange: car 1's first run, both tables fetched.
    set_up_avf(harness)
    job_id = fetched_avf_job(harness)

    # Act: mark the speed trap before the largada (a slip, or a camera
    # that filmed the trap first); the job page warns, never blocks.
    edit_review(harness, job_id, largada_s=3.0, speed_mark_s=2.0)

    # Assert: saved as typed, and drawn from the mark.
    speed = harness.job(job_id)["pass_speed"]
    assert speed["speed_mark_s"] == 2.0, "the orchestrator stores it"
    assert speed["render"]["speed_mark_s"] == 2.0, "and the render uses it as marked"


# --- The render task ---


def ready_avf_job(harness, master) -> str:
    """Car 1's first run reviewed with both marks and ready to render."""
    job_id = fetched_avf_job(harness, worker=master)
    edit_review(
        harness,
        job_id,
        action="ready",
        cut_in_s=0.5,
        cut_out_s=5.0,
        largada_s=LARGADA_S,
        speed_mark_s=SPEED_MARK_S,
    )
    return job_id


def test_the_process_task_carries_the_speed_and_needs_the_speed_feature(harness):
    # Arrange: a 0.8.0 master holding car 1's ready AVF job.
    set_up_avf(harness)
    master = enroll_master(harness, FEATURES_0_8_0)
    job_id = ready_avf_job(harness, master)

    # Act: the master claims the render.
    _, task = claim_ok(harness, master, job_id, TaskKind.PROCESS)

    # Assert: the task has the clock and the speed, and needs "speed".
    assert task["pass_speed"] == SPEED_37_2, "the task must carry the speed as drawn"
    assert task["pass_clock"]["official_units"] == 435, "with the clock above it (4,35)"
    assert "speed" in task["required_render_features"], "only 0.8.0 workers may draw it"


def test_a_worker_before_0_8_0_is_not_offered_a_render_with_a_speed(harness):
    # Arrange: a 0.7.0 master holding car 1's ready AVF job with a speed.
    set_up_avf(harness)
    master = enroll_master(harness, FEATURES_0_7_0)
    job_id = ready_avf_job(harness, master)

    # Act: it asks for work, tries to claim the render anyway, and the
    # sweeper (every 5 s) works out why the job is waiting.
    offered = ask_next(harness, master, [TaskKind.PROCESS])
    refused = claim(harness, master, job_id, TaskKind.PROCESS)
    expire_leases_once(harness.context)

    # Assert: no offer, claim refused, and the job says which version to install.
    assert offered is None, "a 0.7.0 worker must not be offered a speed render"
    assert refused.status_code == 409, "nor may it claim one"
    assert "speed" in refused.json()["error"]["message"], "the refusal must name the feature"
    waiting = harness.job(job_id)["waiting_reason"]
    assert waiting == NO_HOLDER_CAN_RENDER_SPEED, "the job must ask for 0.8.0"


def test_a_job_without_a_speed_mark_goes_to_a_0_7_0_worker_as_before(harness):
    # Arrange: car 1's AVF job with a largada but no speed mark, ready.
    set_up_avf(harness)
    master = enroll_master(harness, FEATURES_0_7_0)
    job_id = fetched_avf_job(harness, worker=master)
    edit_review(harness, job_id, action="ready", cut_in_s=0.5, cut_out_s=5.0, largada_s=LARGADA_S)

    # Act: the 0.7.0 master claims it.
    _, task = claim_ok(harness, master, job_id, TaskKind.PROCESS)

    # Assert: a clock and no speed, so nothing new is needed.
    assert task["pass_speed"] is None, "no mark, no speed in the task"
    assert "speed" not in task["required_render_features"], "a 0.7.0 worker can draw it"


# --- The live blocks (pass-preview) ---


def new_video(harness) -> str:
    """A video in the review queue with nothing typed yet: no prova, car or passagem."""
    worker = harness.enroll(WorkerRole.MASTER, "master")
    job_id = receive_job(harness)
    hold_raw(harness, worker, job_id)
    make_proxy(harness, worker, job_id)
    return job_id


def test_choosing_avf_on_a_new_video_shows_the_speed_without_a_save(harness):
    # Arrange: AVF configured; a new video, nothing typed, no table ever
    # fetched (no other AVF job exists).
    set_up_avf(harness)
    job_id = new_video(harness)

    # Act: the form says AVF, car 1, passagem 1 (nothing saved).
    response = harness.client.get(
        f"/jobs/{job_id}/pass-preview",
        params={"prova": AVF_NAME, "car_number": 1, "passagem": 1},
        auth=harness.reviewer_auth,
    )

    # Assert: both tables were fetched for the preview, and the answer has
    # the clock (4,35) and the speed (37,2 km/h) with no marks yet.
    assert response.status_code == 200, response.text
    preview = response.json()
    assert preview["pass_clock"]["official_text"] == "4,35", "the clock block's stop"
    speed = preview["pass_speed"]
    assert (speed["configured"], speed["official_text"]) == (True, "37,2"), "the speed block"
    assert speed["render"] is None, "no marks yet, so nothing drawn"
    asked = " ".join(query for _, query in harness.results_api.requests)
    assert f"id={CLOCK_TABLE}" in asked, "the preview must fetch the clock's table"
    assert f"id={SPEED_TABLE}" in asked, "and the speed's table"
    assert harness.job(job_id)["prova"] is None, "nothing was saved by asking"
