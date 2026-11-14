"""The rules around waiting for the official result (LIVESTACK-62).

What this module is for: test_result_hold.py tells the main story ("Pronto"
holds the render until the result, the deadline or "Renderizar agora").
These tests prove the rules around it:

  * with a running clock the wait includes the clock's official time, and
    with nothing to draw from the results there is no wait at all (a prova
    without a results configuration, the result and the clock switched off);
  * a review change on a held job is checked at once: switching the result
    and the clock off drops the hold, a passagem that already has its
    result releases it; taking the prova out of the configuration drops it
    on the sweeper's next pass;
  * a held job's waiting reason is the result, never "waiting for a
    worker"; the results fetcher keeps fetching for held jobs;
  * holds survive an orchestrator restart; discarding a job ends its hold;
  * the queue shows the deadline.

What they do not cover: the Settings value (test_result_hold_settings.py)
and the review UI (tests/review_ui/test_result_hold_page.py).
"""

from orchestrator.background.sweepers import expire_leases_once
from orchestrator.domain.pass_results_fetch import poll_due_tables, tables_needing_results
from orchestrator.domain.result_hold import WAITING_FOR_OFFICIAL_RESULT
from orchestrator.domain.review_queue import NO_HOLDER_ONLINE
from orchestrator.testing.harness import build_harness
from orchestrator.testing.scenarios import ask_next, ensure_prova
from shared.models import TaskKind
from tests.orchestrator.pass_results_data import save_config
from tests.orchestrator.result_hold_helpers import (
    CAR_5_RUN_2,
    DEFAULT_WAIT,
    as_time,
    click_pronto,
    event_kinds,
    job_waiting_for_review,
    manobrabilidade_and_master,
)
from tests.orchestrator.test_pass_results_render import judges_publish
from tests.orchestrator.test_render_inputs import edit_review

# Where the reviewer marked the start, in raw-video seconds.
LARGADA_S = 1.5

# --- The clock, and nothing to wait for ---


def test_with_the_result_off_a_clock_still_waits_for_its_official_time(harness):
    # Arrange: car 5's second run in review; no time entered yet.
    master = manobrabilidade_and_master(harness)
    job_id = job_waiting_for_review(harness, master, car=5, passagem=2)

    # Act: "Pronto" with the result line off, the largada marked (clock on by default).
    job = click_pronto(harness, job_id, show_pass_result=False, largada_s=LARGADA_S)

    # Assert: held, because the clock needs the official time to know where to stop.
    assert job["result_hold_until"] is not None, "the clock's official time is worth waiting for"
    assert ask_next(harness, master, [TaskKind.PROCESS]) is None

    # Act again: the judges enter the run (60 s).
    judges_publish(harness, 5, 2, *CAR_5_RUN_2)

    # Assert: released, and the render carries the clock (600 tenths) without a result line.
    task = ask_next(harness, master, [TaskKind.PROCESS])
    assert task["pass_clock"]["official_units"] == 600, "the clock stops on the official 60,0"
    assert task["pass_result_text"] is None, "the reviewer switched the result line off"


def test_a_clock_without_a_largada_and_no_result_line_means_no_wait(harness):
    # Arrange: car 5's second run in review.
    master = manobrabilidade_and_master(harness)
    job_id = job_waiting_for_review(harness, master, car=5, passagem=2)

    # Act: "Pronto" with the result off and no largada marked: no clock is drawn.
    job = click_pronto(harness, job_id, show_pass_result=False)

    # Assert: nothing from the results would be in the clip, so nothing to wait for.
    assert job["result_hold_until"] is None, "no hold when nothing would be drawn"
    assert ask_next(harness, master, [TaskKind.PROCESS])["job_id"] == job_id


def test_a_prova_without_a_results_configuration_is_never_held(harness):
    # Arrange: an Enduro video (no results configuration for Enduro).
    master = manobrabilidade_and_master(harness)
    job_id = job_waiting_for_review(harness, master, car=5, passagem=2)
    ensure_prova(harness, "Enduro")

    # Act: "Pronto" with the box ticked (the page hides it; a script could still send it).
    job = click_pronto(harness, job_id, prova="Enduro")

    # Assert: no result and no clock exist for Enduro, so no hold.
    assert job["pass_result"]["configured"] is False, "Enduro has no result line"
    assert job["result_hold_until"] is None, "a prova without results is never held"


# --- Review changes on a held job ---


def test_switching_the_result_and_the_clock_off_drops_the_hold(harness):
    # Arrange: car 5's job held for its result.
    master = manobrabilidade_and_master(harness)
    job_id = job_waiting_for_review(harness, master, car=5, passagem=2)
    click_pronto(harness, job_id)

    # Act: a reviewer unticks "Mostrar resultado da passagem" and "Mostrar cronômetro" and saves.
    job = edit_review(harness, job_id, show_pass_result=False, show_clock=False)

    # Assert: waiting is pointless now, so the hold is gone and the render offered.
    assert job["result_hold_until"] is None, "nothing left to wait for"
    assert "result_hold_dropped" in event_kinds(harness, job_id)
    assert ask_next(harness, master, [TaskKind.PROCESS])["job_id"] == job_id


def test_a_save_that_moves_to_a_passagem_with_a_result_releases_at_once(harness):
    # Arrange: held as car 5's second run (no result yet).
    master = manobrabilidade_and_master(harness)
    job_id = job_waiting_for_review(harness, master, car=5, passagem=2)
    click_pronto(harness, job_id)

    # Act: the reviewer notices it is the first run, which has "61 s · 3 cones → 76 s".
    job = edit_review(harness, job_id, passagem=1)

    # Assert: the result is there, so the render goes now, with it.
    assert job["result_hold_until"] is None, "the first run's result is already in"
    assert "result_hold_released_result_arrived" in event_kinds(harness, job_id)
    task = ask_next(harness, master, [TaskKind.PROCESS])
    assert task["pass_result_text"] == "61 s · 3 cones → 76 s"


def test_a_save_that_changes_nothing_relevant_keeps_the_hold(harness):
    # Arrange: car 5's job held for its result.
    master = manobrabilidade_and_master(harness)
    job_id = job_waiting_for_review(harness, master, car=5, passagem=2)
    held = click_pronto(harness, job_id)

    # Act: the reviewer only moves the cut-out and saves (the job stays editable).
    job = edit_review(harness, job_id, cut_out_s=4.5)

    # Assert: still held, with the same deadline.
    assert job["result_hold_until"] == held["result_hold_until"], "the hold must stay as it was"
    assert job["cut_out_s"] == 4.5, "the edit itself is saved"


def test_taking_the_prova_out_of_the_configuration_drops_the_hold(harness):
    # Arrange: car 5's job held for its result.
    master = manobrabilidade_and_master(harness)
    job_id = job_waiting_for_review(harness, master, car=5, passagem=2)
    click_pronto(harness, job_id)

    # Act: the admin empties the results configuration; the sweeper runs (every 5 s).
    assert save_config(harness, "").status_code == 200
    expire_leases_once(harness.context)

    # Assert: nothing can come from the results now, so the render goes.
    assert harness.job(job_id)["result_hold_until"] is None, "the hold is dropped"
    assert ask_next(harness, master, [TaskKind.PROCESS])["job_id"] == job_id


def test_discarding_a_held_job_ends_its_hold_for_good(harness):
    # Arrange: car 5's job held for its result.
    master = manobrabilidade_and_master(harness)
    job_id = job_waiting_for_review(harness, master, car=5, passagem=2)
    click_pronto(harness, job_id)

    # Act: discarded, then restored (back to ready).
    edit_review(harness, job_id, action="discard")
    job = edit_review(harness, job_id, action="restore")

    # Assert: the restored job is ready and not held (a fresh "Pronto" decides again).
    assert job["state"] == "ready"
    assert job["result_hold_until"] is None, "a discard ends the hold"


# --- What the job says, and the results fetcher ---


def test_a_held_jobs_waiting_reason_is_the_result_not_a_worker(harness):
    # Arrange: car 5's job held; then the master goes quiet for a minute.
    master = manobrabilidade_and_master(harness)
    job_id = job_waiting_for_review(harness, master, car=5, passagem=2)
    click_pronto(harness, job_id)
    harness.clock.advance(60)

    # Act: the sweeper refreshes the waiting reasons.
    expire_leases_once(harness.context)

    # Assert: it waits for the result, not for a PC.
    assert harness.job(job_id)["waiting_reason"] == WAITING_FOR_OFFICIAL_RESULT

    # Act again: the deadline passes with the master still offline.
    harness.clock.advance(DEFAULT_WAIT.total_seconds())
    expire_leases_once(harness.context)

    # Assert: released, it now waits for a PC like any ready job.
    assert harness.job(job_id)["waiting_reason"] == NO_HOLDER_ONLINE


def test_the_results_fetcher_keeps_fetching_for_a_held_job(harness):
    # Arrange: car 5's held job is the only Manobrabilidade job.
    master = manobrabilidade_and_master(harness)
    job_id = job_waiting_for_review(harness, master, car=5, passagem=2)
    click_pronto(harness, job_id)

    # Act: ask which tables the background loop fetches.
    tables = tables_needing_results(harness.context, harness.clock.now())

    # Assert: the held job's table is among them, so its result can arrive.
    assert tables == ["26BR_MAN"], "a held job counts as active for the fetcher"


def test_the_queue_shows_until_when_a_job_waits(harness):
    # Arrange: car 5's job held for its result.
    master = manobrabilidade_and_master(harness)
    job_id = job_waiting_for_review(harness, master, car=5, passagem=2)
    held = click_pronto(harness, job_id)

    # Act: the review UI lists the ready jobs.
    items = harness.client.get(
        "/review/queue", params={"state": "ready"}, auth=harness.reviewer_auth
    ).json()["items"]

    # Assert: the row carries the deadline, for the "⏳ aguardando resultado" marker.
    assert [item["result_hold_until"] for item in items] == [held["result_hold_until"]]


# --- Restarts ---


def test_an_orchestrator_restart_keeps_the_hold(tmp_path):
    # Arrange: car 5's job held, then the orchestrator stops.
    first = build_harness(tmp_path / "data")
    master = manobrabilidade_and_master(first)
    job_id = job_waiting_for_review(first, master, car=5, passagem=2)
    held = click_pronto(first, job_id)
    stopped_at = first.clock.now()
    first.client.close()
    first.context.database.close()

    # Act: it starts again on the same data folder, a minute later.
    restarted = build_harness(tmp_path / "data", clock_start=stopped_at)
    restarted.clock.advance(60)
    restarted.heartbeat(master, tailnet_address="100.70.0.1", held_job_ids=[job_id])
    poll_due_tables(restarted.context)
    expire_leases_once(restarted.context)

    # Assert: still held, same deadline, nothing offered.
    job = restarted.job(job_id)
    assert job["result_hold_until"] == held["result_hold_until"], "the hold is in the DB"
    assert ask_next(restarted, master, [TaskKind.PROCESS]) is None

    # Act again: the deadline passes after the restart.
    restarted.clock.advance(DEFAULT_WAIT.total_seconds())
    restarted.heartbeat(master, tailnet_address="100.70.0.1", held_job_ids=[job_id])
    expire_leases_once(restarted.context)

    # Assert: released as usual.
    assert as_time(restarted.job(job_id)["result_hold_until"]) is None
    assert ask_next(restarted, master, [TaskKind.PROCESS])["job_id"] == job_id
    restarted.client.close()
    restarted.context.database.close()
