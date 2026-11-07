"""The result line on the job page, in the render task, and in "no longer matches" (LIVESTACK-47).

The story: the audience wants the official result of the passagem on the
clip. The orchestrator builds the line from the latest results it fetched
(test_pass_results_settings.py covers the fetching) and:

* shows it on the job page with the "Mostrar resultado da passagem"
  toggle (default from the prova's configuration), or says why there is
  none yet;
* hands it to the worker in the process task, but only to a worker that
  declared it can draw it (render feature "pass_result", installer
  0.5.0); an older worker would ignore it and render without the pill;
* treats it as a render input: a clip rendered without a result, or with
  an older one, is flagged as no longer matching. It is never
  reprocessed automatically: that stays the reviewer's decision.

Everything goes through the HTTP API, with the real results of the 2026
Manobrabilidade served by the fake results API.
"""

import hashlib

from orchestrator.background.sweepers import expire_leases_once
from orchestrator.domain.pass_results_fetch import poll_due_tables
from orchestrator.domain.render_features import (
    NO_HOLDER_CAN_RENDER,
    NO_HOLDER_CAN_RENDER_PASS_RESULT,
)
from orchestrator.testing.scenarios import (
    SAMPLE_CLIP,
    ask_next,
    claim,
    claim_ok,
    hold_raw,
    make_proxy,
    receive_job,
    save_review_fields,
)
from shared.models import JobState, TaskKind, WorkerRole
from tests.orchestrator.pass_results_data import (
    MANOBRABILIDADE_CONFIG_TEXT,
    RESULTADO_ID,
    car_row,
    job_in_review,
    save_config,
    set_up_manobrabilidade,
)
from tests.orchestrator.test_render_inputs import edit_review, report_inputs

# What each installer version declares (worker/render_features.py).
FEATURES_0_4_0 = ["framing", "no_audio"]
FEATURES_0_5_0 = ["framing", "no_audio", "pass_result"]


def enroll_master(harness, features):
    """The master, beating with the render features of its installer version."""
    master = harness.enroll(WorkerRole.MASTER, "master", heartbeat=False)
    harness.heartbeat(master, tailnet_address="100.70.0.1", render_features=features)
    return master


def ready_job(harness, master, car=26, passagem=2) -> str:
    """A Manobrabilidade job reviewed and ready, with the table fetched."""
    job_id = job_in_review(harness, car, passagem, worker=master)
    poll_due_tables(harness.context)
    edit_review(harness, job_id, action="ready", cut_in_s=1.0, cut_out_s=5.0)
    return job_id


def deliver(harness, master, studio, job_id, *, report=True) -> dict:
    """The master claims, reports what it drew (as 0.5.0 does), and the studio ACKs."""
    lease_id, task = claim_ok(harness, master, job_id, TaskKind.PROCESS)
    sha256 = hashlib.sha256(SAMPLE_CLIP).hexdigest()
    if report:
        response = report_inputs(
            harness, master, job_id, lease_id, task, sha256,
            pass_result_text=task["pass_result_text"],
        )  # fmt: skip
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
    return task


def judges_publish(harness, car, passagem, time, corrected, display):
    """The judges enter (or correct) a run; the next fetch, 30 s later, sees it."""
    row = car_row(harness.results_api.tables[RESULTADO_ID], car)
    row["cells"][3]["raw"][passagem - 1] = time
    row["cells"][4]["raw"][passagem - 1] = corrected
    lines = row["cells"][3]["display"].split("<br />")
    lines[passagem - 1] = display
    row["cells"][3]["display"] = "<br />".join(lines)
    harness.clock.advance(30)
    poll_due_tables(harness.context)


# --- The job page ---


def test_the_job_page_shows_the_line_that_will_be_burned_in(harness):
    # Setup: Manobrabilidade configured, car 26's job in review, table fetched.
    set_up_manobrabilidade(harness)
    job_id = job_in_review(harness, car=26, passagem=2)
    poll_due_tables(harness.context)

    result = harness.job(job_id)["pass_result"]

    # The toggle is offered and on (the prova's show_by_default), and the
    # text is the one the overlay will show.
    assert result["configured"] is True
    assert result["show"] is True
    assert result["text"] == "66 s · 4 cones → 86 s"
    assert result["problem"] is None
    assert harness.job(job_id)["show_pass_result"] is None  # never set by a reviewer


def test_the_job_page_says_why_there_is_no_line_yet(harness):
    set_up_manobrabilidade(harness)

    # Before the first fetch: "not fetched".
    job_id = job_in_review(harness, car=5, passagem=2)
    assert harness.job(job_id)["pass_result"]["problem"] == "not_fetched"

    # Fetched, but car 5 did not do its second run ("-"): "no result yet".
    poll_due_tables(harness.context)
    result = harness.job(job_id)["pass_result"]
    assert (result["text"], result["problem"]) == (None, "no_result_yet")


def test_a_job_without_its_passagem_is_an_incomplete_review(harness):
    # The reviewer picked the prova and the car but no passagem yet.
    set_up_manobrabilidade(harness)
    master = harness.enroll(WorkerRole.MASTER, "master")
    job_id = receive_job(harness)
    hold_raw(harness, master, job_id)
    make_proxy(harness, master, job_id)
    save_review_fields(harness, job_id, prova="Manobrabilidade", car_number=26)
    poll_due_tables(harness.context)

    assert harness.job(job_id)["pass_result"]["problem"] == "incomplete_review"


def test_a_prova_without_configuration_gets_no_line_and_no_toggle(harness):
    set_up_manobrabilidade(harness)
    master = enroll_master(harness, FEATURES_0_5_0)
    job_id = job_in_review(harness, worker=master)
    edit_review(harness, job_id, prova="Enduro")

    result = harness.job(job_id)["pass_result"]
    assert (result["configured"], result["show"], result["text"]) == (False, False, None)
    assert result["problem"] == "not_configured"


def test_the_reviewer_can_turn_the_line_off_and_on(harness):
    set_up_manobrabilidade(harness)
    job_id = job_in_review(harness)
    poll_due_tables(harness.context)

    edit_review(harness, job_id, show_pass_result=False)
    job = harness.job(job_id)
    assert job["show_pass_result"] is False
    # Off: nothing is burned in, but the page still shows what would be.
    assert (job["pass_result"]["show"], job["pass_result"]["text"]) == (
        False,
        "66 s · 4 cones → 86 s",
    )

    edit_review(harness, job_id, show_pass_result=True)
    assert harness.job(job_id)["pass_result"]["show"] is True


def test_show_by_default_false_starts_the_toggle_off(harness):
    set_up_manobrabilidade(
        harness,
        config_text=MANOBRABILIDADE_CONFIG_TEXT.replace(
            '"show_by_default": true', '"show_by_default": false'
        ),
    )
    job_id = job_in_review(harness)
    assert harness.job(job_id)["pass_result"]["show"] is False


# --- The render task and which workers get it ---


def test_a_0_5_0_worker_gets_the_text_in_the_process_task(harness):
    set_up_manobrabilidade(harness)
    master = enroll_master(harness, FEATURES_0_5_0)
    job_id = ready_job(harness, master)

    task = ask_next(harness, master, [TaskKind.PROCESS])

    # The resolved text, and the feature it needs, travel with the task.
    assert task["job_id"] == job_id
    assert task["pass_result_text"] == "66 s · 4 cones → 86 s"
    assert task["required_render_features"] == ["pass_result"]


def test_a_0_4_0_worker_is_not_offered_a_render_with_a_result(harness):
    # Setup: the only holder runs 0.4.0, which cannot draw the result pill.
    set_up_manobrabilidade(harness)
    master = enroll_master(harness, FEATURES_0_4_0)
    job_id = ready_job(harness, master)

    # No offer, and a claim is refused: it would render without the pill.
    assert ask_next(harness, master, [TaskKind.PROCESS]) is None
    refused = claim(harness, master, job_id, TaskKind.PROCESS)
    assert refused.status_code == 409
    assert "pass_result" in refused.json()["error"]["message"]

    # The job says what it waits for: a worker updated to 0.5.0.
    expire_leases_once(harness.context)
    assert harness.job(job_id)["waiting_reason"] == NO_HOLDER_CAN_RENDER_PASS_RESULT

    # Once that PC is updated (its next heartbeat declares pass_result), it
    # gets the render, and the waiting reason goes away.
    harness.heartbeat(
        master,
        tailnet_address="100.70.0.1",
        render_features=FEATURES_0_5_0,
        held_job_ids=[job_id],  # it still has the raw
    )
    expire_leases_once(harness.context)
    assert harness.job(job_id)["waiting_reason"] is None
    assert ask_next(harness, master, [TaskKind.PROCESS])["job_id"] == job_id


def test_without_a_result_any_worker_renders_and_the_pill_is_simply_absent(harness):
    # Car 5 did not do its second run: no text, so nothing new is needed
    # and a 0.4.0 worker may render it (rendering without a result is allowed).
    set_up_manobrabilidade(harness)
    master = enroll_master(harness, FEATURES_0_4_0)
    job_id = ready_job(harness, master, car=5, passagem=2)

    task = ask_next(harness, master, [TaskKind.PROCESS])
    assert task["job_id"] == job_id
    assert task["pass_result_text"] is None
    assert task["required_render_features"] == []


def test_a_job_whose_toggle_is_off_needs_no_new_worker(harness):
    set_up_manobrabilidade(harness)
    master = enroll_master(harness, FEATURES_0_4_0)
    job_id = job_in_review(harness, worker=master)
    poll_due_tables(harness.context)
    edit_review(harness, job_id, action="ready", show_pass_result=False, cut_in_s=1, cut_out_s=5)

    task = ask_next(harness, master, [TaskKind.PROCESS])
    assert (task["pass_result_text"], task["required_render_features"]) == (None, [])


def test_a_holder_that_cannot_do_the_other_features_either_gets_the_older_reason(harness):
    # A 0.3.0 worker (no features at all) holding an upright video with a
    # result: the general "update the worker" reason, not the 0.5.0 one,
    # since even the result aside it could not render it.
    set_up_manobrabilidade(harness)
    master = enroll_master(harness, None)
    job_id = job_in_review(harness, worker=master)
    poll_due_tables(harness.context)
    edit_review(harness, job_id, action="ready", rotation=90, cut_in_s=1, cut_out_s=5)
    expire_leases_once(harness.context)
    assert harness.job(job_id)["waiting_reason"] == NO_HOLDER_CAN_RENDER


# --- Results that arrive or change after the clip was made ---


def test_a_result_published_after_delivery_flags_the_clip(harness):
    # Setup: car 5's second run has no result yet; its clip is rendered
    # without the pill and delivered.
    set_up_manobrabilidade(harness)
    master = enroll_master(harness, FEATURES_0_5_0)
    studio = harness.enroll(WorkerRole.STUDIO, "studio", tailnet_address="100.70.0.3")
    job_id = ready_job(harness, master, car=5, passagem=2)
    deliver(harness, master, studio, job_id)
    assert harness.job(job_id)["clip_inputs"]["changed"] == []

    # What it does: the judges enter the run; the next fetch sees it.
    judges_publish(harness, 5, 2, "60 (2 )", 70, "60 (2<img class='cone'>)")

    # The clip on air no longer matches: no result then, the result now.
    job = harness.job(job_id)
    assert job["clip_inputs"]["changed"] == ["pass_result"]
    assert job["clip_inputs"]["clip"]["pass_result_text"] is None
    assert job["clip_inputs"]["now"]["pass_result_text"] == "60 s · 2 cones → 70 s"
    # It is in "Clipes desatualizados"...
    outdated = harness.client.get(
        "/review/queue", params={"state": "done", "outdated": "true"}, auth=harness.reviewer_auth
    ).json()["items"]
    assert [item["id"] for item in outdated] == [job_id]
    # ...and nothing was reprocessed: the job is still done, the version 1.
    assert job["state"] == JobState.DONE
    assert len(job["clip_versions"]) == 1


def test_a_result_corrected_after_delivery_flags_the_clip(harness):
    set_up_manobrabilidade(harness)
    master = enroll_master(harness, FEATURES_0_5_0)
    studio = harness.enroll(WorkerRole.STUDIO, "studio", tailnet_address="100.70.0.3")
    job_id = ready_job(harness, master, car=26, passagem=2)
    deliver(harness, master, studio, job_id)
    assert harness.job(job_id)["clip_inputs"]["clip"]["pass_result_text"] == (
        "66 s · 4 cones → 86 s"
    )

    # The judges find a fifth cone.
    judges_publish(harness, 26, 2, "66 (5 )", 91, "66 (5<img class='cone'>)")

    job = harness.job(job_id)
    assert job["clip_inputs"]["changed"] == ["pass_result"]
    assert job["clip_inputs"]["now"]["pass_result_text"] == "66 s · 5 cones → 91 s"


def test_a_correction_the_next_morning_still_flags_the_clip(harness):
    # Delivered in the afternoon; the event goes on (answers cached 5 s),
    # so the table is still fetched however long ago the clip went out.
    set_up_manobrabilidade(harness)
    master = enroll_master(harness, FEATURES_0_5_0)
    studio = harness.enroll(WorkerRole.STUDIO, "studio", tailnet_address="100.70.0.3")
    job_id = ready_job(harness, master, car=26, passagem=2)
    deliver(harness, master, studio, job_id)
    harness.clock.advance(16 * 3600)
    poll_due_tables(harness.context)

    judges_publish(harness, 26, 2, "66 (5 )", 91, "66 (5<img class='cone'>)")

    assert harness.job(job_id)["clip_inputs"]["changed"] == ["pass_result"]


def test_turning_the_line_off_after_delivery_flags_the_clip_and_back_on_clears_it(harness):
    set_up_manobrabilidade(harness)
    master = enroll_master(harness, FEATURES_0_5_0)
    studio = harness.enroll(WorkerRole.STUDIO, "studio", tailnet_address="100.70.0.3")
    job_id = ready_job(harness, master)
    deliver(harness, master, studio, job_id)

    edit_review(harness, job_id, show_pass_result=False)
    assert harness.job(job_id)["clip_inputs"]["changed"] == ["pass_result"]
    edit_review(harness, job_id, show_pass_result=True)
    assert harness.job(job_id)["clip_inputs"]["changed"] == []


def test_without_a_worker_report_the_claim_snapshot_keeps_the_text(harness):
    # A worker that reports nothing (or whose report was refused): the
    # version keeps what the orchestrator handed out at the claim.
    set_up_manobrabilidade(harness)
    master = enroll_master(harness, FEATURES_0_5_0)
    studio = harness.enroll(WorkerRole.STUDIO, "studio", tailnet_address="100.70.0.3")
    job_id = ready_job(harness, master)
    deliver(harness, master, studio, job_id, report=False)

    clip = harness.job(job_id)["clip_inputs"]["clip"]
    assert clip["reported_by_worker"] is False
    assert clip["pass_result_text"] == "66 s · 4 cones → 86 s"


def test_a_failed_fetch_never_makes_a_clip_look_out_of_date(harness):
    # The API going down keeps the last good text, so the comparison does
    # not change: nothing on air is flagged (or blanked) by a failure.
    set_up_manobrabilidade(harness)
    master = enroll_master(harness, FEATURES_0_5_0)
    studio = harness.enroll(WorkerRole.STUDIO, "studio", tailnet_address="100.70.0.3")
    job_id = ready_job(harness, master)
    deliver(harness, master, studio, job_id)

    harness.results_api.unreachable = True
    harness.clock.advance(30)
    poll_due_tables(harness.context)

    job = harness.job(job_id)
    assert job["pass_result"]["fetch"]["stale"] is True
    assert job["clip_inputs"]["changed"] == []


def test_removing_the_configuration_flags_clips_that_show_a_result(harness):
    # Taking a prova out of the configuration means new renders have no
    # pill: a clip that has one is flagged (the reviewer decides).
    set_up_manobrabilidade(harness)
    master = enroll_master(harness, FEATURES_0_5_0)
    studio = harness.enroll(WorkerRole.STUDIO, "studio", tailnet_address="100.70.0.3")
    job_id = ready_job(harness, master)
    deliver(harness, master, studio, job_id)

    assert save_config(harness, "").status_code == 200

    assert harness.job(job_id)["clip_inputs"]["changed"] == ["pass_result"]
