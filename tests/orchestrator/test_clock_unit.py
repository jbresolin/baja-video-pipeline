"""The clock's unit, "66,0 s" (LIVESTACK-53), through the orchestrator's HTTP API.

The story: the clip's clock now writes its unit after the digits. The unit
comes from the prova's `clock` block in the pass-results configuration
(`"unit"`, default "s"). The orchestrator:

* tells the job page the unit (Job.pass_clock.unit) and hands it to the
  worker with the clock (ProcessTask.pass_clock.unit);
* treats it as part of the clock in "this clip no longer matches", except
  for clips rendered before the unit existed: their clock has no unit on
  record, and the new look alone must not flag every delivered clip.

Real results of the 2026 Manobrabilidade: car 26's second run, 66 s.
"""

import copy
import hashlib
import json

from orchestrator.testing.scenarios import SAMPLE_CLIP, claim_ok
from shared.models import TaskKind, WorkerRole
from tests.orchestrator.pass_results_data import (
    MANOBRABILIDADE_CONFIG,
    save_config,
    set_up_manobrabilidade,
)
from tests.orchestrator.test_pass_clock import FEATURES_0_6_0, ready_job_with_largada
from tests.orchestrator.test_pass_results_render import enroll_master
from tests.orchestrator.test_render_inputs import report_inputs


def config_with_clock_unit(unit: str) -> str:
    """The test configuration with Manobrabilidade's clock unit set to `unit`."""
    config = copy.deepcopy(MANOBRABILIDADE_CONFIG)
    config["provas"]["MAN"]["clock"]["unit"] = unit
    return json.dumps(config, ensure_ascii=False)


def deliver_reporting(harness, master, studio, job_id, reported_clock) -> dict:
    """The master renders, reports `reported_clock` as what it drew, and the studio ACKs."""
    lease_id, task = claim_ok(harness, master, job_id, TaskKind.PROCESS)
    sha256 = hashlib.sha256(SAMPLE_CLIP).hexdigest()
    response = report_inputs(
        harness, master, job_id, lease_id, task, sha256,
        pass_result_text=task["pass_result_text"], pass_clock=reported_clock,
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


def test_the_unit_defaults_to_seconds_on_the_job_and_in_the_task(harness):
    # The test configuration's clock block has no "unit": the default "s".
    set_up_manobrabilidade(harness)
    master = enroll_master(harness, FEATURES_0_6_0)
    job_id = ready_job_with_largada(harness, master)

    assert harness.job(job_id)["pass_clock"]["unit"] == "s"
    _, task = claim_ok(harness, master, job_id, TaskKind.PROCESS)
    assert task["pass_clock"]["unit"] == "s"


def test_a_configured_unit_reaches_the_job_page_and_the_worker(harness):
    set_up_manobrabilidade(harness, config_text=config_with_clock_unit("seg"))
    master = enroll_master(harness, FEATURES_0_6_0)
    job_id = ready_job_with_largada(harness, master)

    clock = harness.job(job_id)["pass_clock"]
    assert (clock["unit"], clock["render"]["unit"]) == ("seg", "seg")
    _, task = claim_ok(harness, master, job_id, TaskKind.PROCESS)
    assert task["pass_clock"]["unit"] == "seg"


def test_a_clip_rendered_before_the_unit_existed_is_not_out_of_date(harness):
    # Setup: a clip delivered by a worker that reported its clock without a
    # unit, as every clip rendered before 2.10.0 is recorded.
    set_up_manobrabilidade(harness)
    master = enroll_master(harness, FEATURES_0_6_0)
    studio = harness.enroll(WorkerRole.STUDIO, "studio", tailnet_address="100.70.0.3")
    job_id = ready_job_with_largada(harness, master)
    old_report = {"largada_s": 1.5, "official_units": 660, "decimals": 1}

    deliver_reporting(harness, master, studio, job_id, old_report)

    # Now a render would draw "66,0 s"; the clip has no unit on record.
    # Same largada, official time and decimals: the clip still matches.
    comparison = harness.job(job_id)["clip_inputs"]
    assert comparison["clip"]["pass_clock"]["unit"] is None
    assert comparison["now"]["pass_clock"]["unit"] == "s"
    assert comparison["changed"] == []


def test_changing_the_unit_after_delivery_flags_the_clip(harness):
    # Setup: a clip delivered with "66,0 s" on record.
    set_up_manobrabilidade(harness)
    master = enroll_master(harness, FEATURES_0_6_0)
    studio = harness.enroll(WorkerRole.STUDIO, "studio", tailnet_address="100.70.0.3")
    job_id = ready_job_with_largada(harness, master)
    drawn = {"largada_s": 1.5, "official_units": 660, "decimals": 1, "unit": "s"}
    deliver_reporting(harness, master, studio, job_id, drawn)
    assert harness.job(job_id)["clip_inputs"]["changed"] == []

    # The admin changes the unit: a new render would look different.
    assert save_config(harness, config_with_clock_unit("seg")).status_code == 200

    assert harness.job(job_id)["clip_inputs"]["changed"] == ["clock"]
