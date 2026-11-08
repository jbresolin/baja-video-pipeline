"""The running clock on the job page, in the render task, and in "no longer matches" (LIVESTACK-48).

The story: for a timed prova, the clip shows a clock that starts at 0 on
the largada (marked by the reviewer, in raw-video seconds) and freezes on
the official time of the passagem (the results API's raw run time, the
prova's `clock.column`). The orchestrator:

* tells the job page whether the prova has a clock, the official time
  ("66,0"), the "Mostrar cronômetro" toggle and what a render would draw;
* hands the clock to the worker in the process task, but only to a worker
  that declared it can draw it (render feature "clock", installer 0.6.0);
* treats the largada and the official time as render inputs: moving the
  largada, or the official time arriving or changing, flags a delivered
  clip as no longer matching. Reprocessing stays the reviewer's decision.

Everything goes through the HTTP API, with the real results of the 2026
Manobrabilidade (car 26's second run took 66 s, so its clock stops on
"66,0" with the configuration's one decimal).
"""

import hashlib
import json

from orchestrator.background.sweepers import expire_leases_once
from orchestrator.domain.pass_results_fetch import poll_due_tables
from orchestrator.domain.render_features import NO_HOLDER_CAN_RENDER_CLOCK
from orchestrator.testing.scenarios import SAMPLE_CLIP, ask_next, claim, claim_ok
from shared.models import TaskKind, WorkerRole
from tests.orchestrator.pass_results_data import (
    MANOBRABILIDADE_CONFIG,
    job_in_review,
    save_config,
    set_up_manobrabilidade,
)
from tests.orchestrator.test_pass_results_render import enroll_master, judges_publish
from tests.orchestrator.test_render_inputs import edit_review, report_inputs

FEATURES_0_5_0 = ["framing", "no_audio", "pass_result"]
FEATURES_0_6_0 = ["framing", "no_audio", "pass_result", "clock"]
LARGADA_S = 1.5
CLOCK_66 = {"largada_s": LARGADA_S, "official_units": 660, "decimals": 1}


def ready_job_with_largada(harness, master, car=26, passagem=2, **fields) -> str:
    """A Manobrabilidade job with the table fetched, the largada marked, and ready."""
    job_id = job_in_review(harness, car, passagem, worker=master)
    poll_due_tables(harness.context)
    edit_review(
        harness, job_id, action="ready", cut_in_s=1.0, cut_out_s=5.0, largada_s=LARGADA_S, **fields
    )
    return job_id


def deliver(harness, master, studio, job_id, *, report=True) -> dict:
    """The master claims, reports what it drew (as 0.6.0 does), and the studio ACKs."""
    lease_id, task = claim_ok(harness, master, job_id, TaskKind.PROCESS)
    sha256 = hashlib.sha256(SAMPLE_CLIP).hexdigest()
    if report:
        response = report_inputs(
            harness, master, job_id, lease_id, task, sha256,
            pass_result_text=task["pass_result_text"], pass_clock=task["pass_clock"],
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


# --- The job page ---


def test_the_job_page_has_the_official_time_and_waits_for_the_largada(harness):
    # Setup: Manobrabilidade has a clock block; car 26's job, table fetched.
    set_up_manobrabilidade(harness)
    job_id = job_in_review(harness, car=26, passagem=2)
    poll_due_tables(harness.context)

    job = harness.job(job_id)
    clock = job["pass_clock"]

    # Offered and on by default (the prova's show_by_default), the official
    # time known, but no largada marked yet: a render would draw no clock.
    assert (clock["configured"], clock["show"]) == (True, True)
    assert (clock["official_units"], clock["official_text"], clock["decimals"]) == (660, "66,0", 1)
    assert clock["largada_s"] is None
    assert clock["render"] is None
    assert (job["largada_s"], job["show_clock"]) == (None, None)


def test_marking_the_largada_makes_the_clock_part_of_the_render(harness):
    set_up_manobrabilidade(harness)
    job_id = job_in_review(harness)
    poll_due_tables(harness.context)

    edit_review(harness, job_id, largada_s=LARGADA_S)

    job = harness.job(job_id)
    assert job["largada_s"] == LARGADA_S
    assert job["pass_clock"]["render"] == CLOCK_66


def test_the_largada_can_be_removed(harness):
    # "Left out = keep" cannot remove a value, so removing has its own flag.
    set_up_manobrabilidade(harness)
    job_id = job_in_review(harness)
    edit_review(harness, job_id, largada_s=LARGADA_S)
    edit_review(harness, job_id, prova="Manobrabilidade")  # another save keeps it
    assert harness.job(job_id)["largada_s"] == LARGADA_S

    edit_review(harness, job_id, clear_largada=True)

    assert harness.job(job_id)["largada_s"] is None
    assert harness.job(job_id)["pass_clock"]["render"] is None


def test_a_largada_past_the_end_of_the_video_is_refused(harness):
    set_up_manobrabilidade(harness)
    job_id = job_in_review(harness)
    duration = harness.job(job_id)["duration_s"]
    assert duration is not None  # the holder probed the raw

    response = harness.client.patch(
        f"/jobs/{job_id}/review",
        json={"expected_version": harness.job(job_id)["version"], "largada_s": duration + 1},
        auth=harness.reviewer_auth,
    )

    assert response.status_code == 422
    assert "largada_s" in response.json()["error"]["message"]


def test_the_reviewer_can_turn_the_clock_off(harness):
    set_up_manobrabilidade(harness)
    job_id = job_in_review(harness)
    poll_due_tables(harness.context)
    edit_review(harness, job_id, largada_s=LARGADA_S, show_clock=False)

    clock = harness.job(job_id)["pass_clock"]
    # Off: no clock in the render, but the page still knows where it would stop.
    assert (clock["show"], clock["render"], clock["official_text"]) == (False, None, "66,0")
    assert harness.job(job_id)["show_clock"] is False


def test_without_an_official_time_there_is_no_clock(harness):
    # Car 5 did not do its second run ("-"): no official time, no clock,
    # whatever the largada.
    set_up_manobrabilidade(harness)
    job_id = job_in_review(harness, car=5, passagem=2)
    poll_due_tables(harness.context)
    edit_review(harness, job_id, largada_s=LARGADA_S)

    clock = harness.job(job_id)["pass_clock"]
    assert (clock["official_text"], clock["render"]) == (None, None)
    assert clock["problem"] == "no_result_yet"


def test_a_prova_without_a_clock_block_offers_no_clock(harness):
    config = json.loads(json.dumps(MANOBRABILIDADE_CONFIG))
    del config["provas"]["MAN"]["clock"]
    set_up_manobrabilidade(harness, config_text=json.dumps(config))
    job_id = job_in_review(harness)
    edit_review(harness, job_id, largada_s=LARGADA_S)

    clock = harness.job(job_id)["pass_clock"]
    assert (clock["configured"], clock["render"], clock["problem"]) == (
        False,
        None,
        "not_configured",
    )


def test_a_prova_with_only_a_clock_has_a_clock_and_no_result_line(harness):
    # A timed prova whose result line is not wanted: only the clock block.
    config = json.loads(json.dumps(MANOBRABILIDADE_CONFIG))
    del config["provas"]["MAN"]["result"]
    set_up_manobrabilidade(harness, config_text=json.dumps(config))
    job_id = job_in_review(harness)
    poll_due_tables(harness.context)  # its table is fetched all the same
    edit_review(harness, job_id, largada_s=LARGADA_S)

    job = harness.job(job_id)
    assert job["pass_result"]["configured"] is False
    assert job["pass_clock"]["render"] == CLOCK_66
    # The fetch status comes with the clock, since no result block shows it.
    assert job["pass_clock"]["fetch"]["resultado_id"] == "26BR_MAN"


# --- The render task and which workers get it ---


def test_a_0_6_0_worker_gets_the_clock_in_the_process_task(harness):
    set_up_manobrabilidade(harness)
    master = enroll_master(harness, FEATURES_0_6_0)
    job_id = ready_job_with_largada(harness, master)

    task = ask_next(harness, master, [TaskKind.PROCESS])

    assert task["job_id"] == job_id
    assert task["pass_clock"] == CLOCK_66
    assert task["required_render_features"] == ["pass_result", "clock"]


def test_a_0_5_0_worker_is_not_offered_a_render_with_a_clock(harness):
    # Setup: the only holder runs 0.5.0: it draws the result, not the clock.
    set_up_manobrabilidade(harness)
    master = enroll_master(harness, FEATURES_0_5_0)
    job_id = ready_job_with_largada(harness, master)

    # No offer, and a claim is refused: it would render without the clock.
    assert ask_next(harness, master, [TaskKind.PROCESS]) is None
    refused = claim(harness, master, job_id, TaskKind.PROCESS)
    assert refused.status_code == 409
    assert "clock" in refused.json()["error"]["message"]

    # The job says what it waits for: a worker updated to 0.6.0.
    expire_leases_once(harness.context)
    assert harness.job(job_id)["waiting_reason"] == NO_HOLDER_CAN_RENDER_CLOCK

    # Updated (its next heartbeat declares "clock"), it gets the render.
    harness.heartbeat(
        master,
        tailnet_address="100.70.0.1",
        render_features=FEATURES_0_6_0,
        held_job_ids=[job_id],
    )
    expire_leases_once(harness.context)
    assert harness.job(job_id)["waiting_reason"] is None
    assert ask_next(harness, master, [TaskKind.PROCESS])["job_id"] == job_id


def test_with_the_clock_off_a_0_5_0_worker_renders(harness):
    set_up_manobrabilidade(harness)
    master = enroll_master(harness, FEATURES_0_5_0)
    job_id = ready_job_with_largada(harness, master, show_clock=False)

    task = ask_next(harness, master, [TaskKind.PROCESS])
    assert task["job_id"] == job_id
    assert (task["pass_clock"], task["required_render_features"]) == (None, ["pass_result"])


# --- Changes after the clip was made ---


def test_the_official_time_arriving_after_delivery_flags_the_clip(harness):
    # Car 5's second run had no result when its clip was rendered (no pill,
    # no clock, though the largada was marked); then the judges enter it.
    set_up_manobrabilidade(harness)
    master = enroll_master(harness, FEATURES_0_6_0)
    studio = harness.enroll(WorkerRole.STUDIO, "studio", tailnet_address="100.70.0.3")
    job_id = ready_job_with_largada(harness, master, car=5, passagem=2)
    deliver(harness, master, studio, job_id)
    assert harness.job(job_id)["clip_inputs"]["changed"] == []

    judges_publish(harness, 5, 2, "60 (2 )", 70, "60 (2<img class='cone'>)")

    comparison = harness.job(job_id)["clip_inputs"]
    assert comparison["changed"] == ["pass_result", "clock"]
    assert comparison["clip"]["pass_clock"] is None
    assert comparison["now"]["pass_clock"] == {**CLOCK_66, "official_units": 600}


def test_moving_the_largada_after_delivery_flags_the_clip(harness):
    set_up_manobrabilidade(harness)
    master = enroll_master(harness, FEATURES_0_6_0)
    studio = harness.enroll(WorkerRole.STUDIO, "studio", tailnet_address="100.70.0.3")
    job_id = ready_job_with_largada(harness, master)
    deliver(harness, master, studio, job_id)
    assert harness.job(job_id)["clip_inputs"]["clip"]["pass_clock"] == CLOCK_66

    # One frame later (33 ms) is a different clock.
    edit_review(harness, job_id, largada_s=LARGADA_S + 1 / 30)
    assert harness.job(job_id)["clip_inputs"]["changed"] == ["clock"]

    # Back where it was: the clip matches again.
    edit_review(harness, job_id, largada_s=LARGADA_S)
    assert harness.job(job_id)["clip_inputs"]["changed"] == []


def test_a_corrected_run_time_flags_the_clip(harness):
    set_up_manobrabilidade(harness)
    master = enroll_master(harness, FEATURES_0_6_0)
    studio = harness.enroll(WorkerRole.STUDIO, "studio", tailnet_address="100.70.0.3")
    job_id = ready_job_with_largada(harness, master)
    deliver(harness, master, studio, job_id)

    # The judges correct the time itself (67 s): the clock would stop later.
    judges_publish(harness, 26, 2, "67 (4 )", 87, "67 (4<img class='cone'>)")

    comparison = harness.job(job_id)["clip_inputs"]
    assert "clock" in comparison["changed"]
    assert comparison["now"]["pass_clock"]["official_units"] == 670


def test_turning_the_clock_off_after_delivery_flags_the_clip(harness):
    set_up_manobrabilidade(harness)
    master = enroll_master(harness, FEATURES_0_6_0)
    studio = harness.enroll(WorkerRole.STUDIO, "studio", tailnet_address="100.70.0.3")
    job_id = ready_job_with_largada(harness, master)
    deliver(harness, master, studio, job_id)

    edit_review(harness, job_id, show_clock=False)
    assert harness.job(job_id)["clip_inputs"]["changed"] == ["clock"]
    # Nothing was reprocessed: that stays the reviewer's decision.
    assert len(harness.job(job_id)["clip_versions"]) == 1


def test_without_a_worker_report_the_claim_snapshot_keeps_the_clock(harness):
    set_up_manobrabilidade(harness)
    master = enroll_master(harness, FEATURES_0_6_0)
    studio = harness.enroll(WorkerRole.STUDIO, "studio", tailnet_address="100.70.0.3")
    job_id = ready_job_with_largada(harness, master)
    deliver(harness, master, studio, job_id, report=False)

    clip = harness.job(job_id)["clip_inputs"]["clip"]
    assert clip["reported_by_worker"] is False
    assert clip["pass_clock"] == CLOCK_66


# --- "Testar" on the Settings page ---


def test_testar_shows_where_the_clock_stops(harness):
    set_up_manobrabilidade(harness)

    answer = harness.client.post(
        "/admin/pass-results/test",
        json={"prova_id": "MAN", "car_number": 26, "passagem": 2},
        auth=harness.admin_auth,
    ).json()

    assert answer["text"] == "66 s · 4 cones → 86 s"
    assert (answer["clock_configured"], answer["clock_text"]) == (True, "66,0")
    assert answer["clock_problem"] is None


def test_testar_on_a_prova_with_only_a_clock(harness):
    config = json.loads(json.dumps(MANOBRABILIDADE_CONFIG))
    del config["provas"]["MAN"]["result"]
    set_up_manobrabilidade(harness)
    assert save_config(harness, json.dumps(config)).status_code == 200

    answer = harness.client.post(
        "/admin/pass-results/test",
        json={"prova_id": "MAN", "car_number": 5, "passagem": 2},
        auth=harness.admin_auth,
    ).json()

    # No line and no complaint about one; the clock says why it has no time.
    assert (answer["text"], answer["problem"]) == (None, None)
    assert (answer["clock_text"], answer["clock_problem"]) == (None, "no_result_yet")
