""" "Pronto" can wait for the official result before rendering (LIVESTACK-62).

What this module is for: judges often enter a passagem's result a few
minutes after the reviewer has the video ready. With "Esperar o resultado
oficial" ticked next to "Pronto", the job becomes `ready` as always, but
its render is offered to no worker until the first of:

  * the result arrives (the next results fetch sees it);
  * the deadline passes (15 minutes by default): rendered without it,
    and §32 flags the clip once the result comes;
  * a reviewer clicks "Renderizar agora".

What these tests prove: each of those three ways out, the box unticked
(rendered at once, as before), a result that is already there (no wait at
all), and "Salvar e reprocessar" on a delivered clip using the same wait.

What they do not cover: the clock, review changes that make waiting
pointless, restarts and the Settings value (test_result_hold_rules.py);
the job page (tests/review_ui/test_result_hold_page.py).

Everything goes through the HTTP API, like the review UI and the workers.
The orchestrator's clock starts at 2026-10-25 12:00 UTC and only moves
when a test moves it. The results are the real 2026 Manobrabilidade ones:
car 5 has not done its second run yet, car 26 has.
"""

from orchestrator.background.sweepers import expire_leases_once
from orchestrator.testing.scenarios import ask_next, claim
from shared.models import JobState, TaskKind, WorkerRole
from tests.orchestrator.result_hold_helpers import (
    CAR_5_RESULT_TEXT,
    CAR_5_RUN_2,
    DEFAULT_WAIT,
    as_time,
    click_pronto,
    click_render_now,
    event_kinds,
    events_of_kind,
    job_waiting_for_review,
    manobrabilidade_and_master,
)
from tests.orchestrator.test_pass_results_render import deliver, judges_publish
from tests.orchestrator.test_render_inputs import edit_review


def test_pronto_with_the_box_ticked_holds_the_render(harness):
    # Arrange: Manobrabilidade configured; car 5's second run in review, no result entered yet.
    master = manobrabilidade_and_master(harness)
    job_id = job_waiting_for_review(harness, master, car=5, passagem=2)

    # Act: the reviewer clicks "Pronto" with "Esperar o resultado oficial" ticked.
    job = click_pronto(harness, job_id, wait_for_result=True)

    # Assert: the job is ready, but held for 15 minutes, and nobody gets the render.
    assert job["state"] == JobState.READY, "Pronto still makes the job ready"
    assert as_time(job["result_hold_until"]) == harness.clock.now() + DEFAULT_WAIT, (
        "the hold must end 15 minutes after Pronto at the latest"
    )
    assert ask_next(harness, master, [TaskKind.PROCESS]) is None, (
        "a held render must not be offered to the PC that holds the raw"
    )
    refused = claim(harness, master, job_id, TaskKind.PROCESS)
    assert refused.status_code == 409, "a held render cannot be claimed either"
    assert refused.json()["error"]["code"] == "not_claimable"
    assert "result_hold_started" in event_kinds(harness, job_id), "the log says it was held"


def test_the_result_arriving_on_the_next_fetch_releases_the_render(harness):
    # Arrange: car 5's job is held for its result.
    master = manobrabilidade_and_master(harness)
    job_id = job_waiting_for_review(harness, master, car=5, passagem=2)
    click_pronto(harness, job_id)

    # Act: the judges enter the run; the next fetch (30 s later) sees it.
    judges_publish(harness, 5, 2, *CAR_5_RUN_2)

    # Assert: the hold is gone and the render goes out with the result line.
    job = harness.job(job_id)
    assert job["result_hold_until"] is None, "the result must end the hold at once"
    task = ask_next(harness, master, [TaskKind.PROCESS])
    assert task is not None and task["job_id"] == job_id, "the render is offered now"
    assert task["pass_result_text"] == CAR_5_RESULT_TEXT, "and it carries the official result"
    assert "result_hold_released_result_arrived" in event_kinds(harness, job_id)


def test_the_deadline_passing_releases_the_render_without_the_result(harness):
    # Arrange: car 5's job is held; the judges are late.
    master = manobrabilidade_and_master(harness)
    job_id = job_waiting_for_review(harness, master, car=5, passagem=2)
    click_pronto(harness, job_id)

    # Act: 15 minutes go by (the master keeps beating) and the sweeper runs.
    harness.clock.advance(DEFAULT_WAIT.total_seconds())
    harness.heartbeat(master, tailnet_address="100.70.0.1", held_job_ids=[job_id])
    expire_leases_once(harness.context)

    # Assert: the render is offered without a result line.
    assert harness.job(job_id)["result_hold_until"] is None, "the deadline ends the hold"
    task = ask_next(harness, master, [TaskKind.PROCESS])
    assert task["job_id"] == job_id, "the render is offered after the deadline"
    assert task["pass_result_text"] is None, "there is no result to draw yet"
    assert "result_hold_released_deadline_passed" in event_kinds(harness, job_id)


def test_a_clip_rendered_after_the_deadline_is_flagged_when_the_result_comes(harness):
    # Arrange: the deadline passed, the clip was rendered without a result and delivered.
    master = manobrabilidade_and_master(harness)
    studio = harness.enroll(WorkerRole.STUDIO, "studio", tailnet_address="100.70.0.3")
    job_id = job_waiting_for_review(harness, master, car=5, passagem=2)
    click_pronto(harness, job_id)
    harness.clock.advance(DEFAULT_WAIT.total_seconds())
    harness.heartbeat(master, tailnet_address="100.70.0.1", held_job_ids=[job_id])
    harness.heartbeat(studio, tailnet_address="100.70.0.3")
    expire_leases_once(harness.context)
    deliver(harness, master, studio, job_id)

    # Act: the judges finally enter the run.
    judges_publish(harness, 5, 2, *CAR_5_RUN_2)

    # Assert: the clip on air is out of date (design §32); nothing re-renders by itself.
    job = harness.job(job_id)
    assert job["clip_inputs"]["changed"] == ["pass_result"], "the clip must be flagged"
    assert job["state"] == JobState.DONE, "reprocessing stays the reviewer's decision"


def test_renderizar_agora_releases_the_render_at_once(harness):
    # Arrange: car 5's job is held for its result.
    master = manobrabilidade_and_master(harness)
    job_id = job_waiting_for_review(harness, master, car=5, passagem=2)
    held = click_pronto(harness, job_id)

    # Act: a reviewer does not want to wait and clicks "Renderizar agora".
    response = click_render_now(harness, job_id)

    # Assert: answered with the released job, offered at once, logged under the reviewer.
    assert response.status_code == 200, response.text
    assert response.json()["result_hold_until"] is None, "the hold must be gone"
    assert response.json()["version"] == held["version"], "not a review change: same version"
    assert ask_next(harness, master, [TaskKind.PROCESS])["job_id"] == job_id
    released = events_of_kind(harness, job_id, "result_hold_released_by_reviewer")
    assert [event["actor"] for event in released] == ["reviewer:ana"], "the log names who"


def test_renderizar_agora_on_a_job_that_is_not_held_changes_nothing(harness):
    # Arrange: a job that was never held (the box was unticked).
    master = manobrabilidade_and_master(harness)
    job_id = job_waiting_for_review(harness, master, car=5, passagem=2)
    click_pronto(harness, job_id, wait_for_result=False)

    # Act: a second reviewer's late click (the page was stale).
    response = click_render_now(harness, job_id)

    # Assert: a harmless answer, and nothing new in the log.
    assert response.status_code == 200, "a second click must not be an error"
    assert "result_hold_released_by_reviewer" not in event_kinds(harness, job_id)


def test_pronto_with_the_box_unticked_renders_at_once(harness):
    # Arrange: car 5's job in review, no result yet.
    master = manobrabilidade_and_master(harness)
    job_id = job_waiting_for_review(harness, master, car=5, passagem=2)

    # Act: "Pronto" with the box unticked (an older page or a script leaves it out too).
    job = click_pronto(harness, job_id, wait_for_result=False)

    # Assert: no hold; the render is offered straight away, as before.
    assert job["result_hold_until"] is None, "an unticked box must not hold the render"
    assert ask_next(harness, master, [TaskKind.PROCESS])["job_id"] == job_id


def test_a_result_already_there_means_no_wait_at_all(harness):
    # Arrange: car 26's second run already has its result in the fetched table.
    master = manobrabilidade_and_master(harness)
    job_id = job_waiting_for_review(harness, master, car=26, passagem=2)

    # Act: "Pronto" with the box ticked.
    job = click_pronto(harness, job_id)

    # Assert: nothing to wait for, so no hold and no "held" line in the log.
    assert job["result_hold_until"] is None, "waiting could not change this clip"
    assert "result_hold_started" not in event_kinds(harness, job_id)
    assert ask_next(harness, master, [TaskKind.PROCESS])["job_id"] == job_id


def test_salvar_e_reprocessar_on_a_delivered_clip_uses_the_same_wait(harness):
    # Arrange: car 5's clip went out without a result (the box unticked).
    master = manobrabilidade_and_master(harness)
    studio = harness.enroll(WorkerRole.STUDIO, "studio", tailnet_address="100.70.0.3")
    job_id = job_waiting_for_review(harness, master, car=5, passagem=2)
    click_pronto(harness, job_id, wait_for_result=False)
    deliver(harness, master, studio, job_id)

    # Act: the reviewer fixes the cut with "Salvar e reprocessar", box ticked.
    job = edit_review(harness, job_id, action="reprocess", cut_in_s=2.0, wait_for_result=True)

    # Assert: back to ready, held for the result like a first "Pronto".
    assert job["state"] == JobState.READY
    assert as_time(job["result_hold_until"]) == harness.clock.now() + DEFAULT_WAIT
    assert ask_next(harness, master, [TaskKind.PROCESS]) is None, "the new render waits too"

    # Act again: the result arrives.
    judges_publish(harness, 5, 2, *CAR_5_RUN_2)

    # Assert: the second version is rendered with it.
    task = ask_next(harness, master, [TaskKind.PROCESS])
    assert task["pass_result_text"] == CAR_5_RESULT_TEXT, "the new clip gets the result"
