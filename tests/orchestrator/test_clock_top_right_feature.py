"""Which workers may draw a clip with a result or a clock after LIVESTACK-53
(render feature "clock_top_right").

The story: installer 0.7.0 draws the new look: the clock top right,
bigger, "66,0 s"; the result pill from the clock's freeze; the time's
unit small in the result pill ("66 s"). A 0.6.0 worker draws the old one
(the clock small beside the result, the result from the first frame, a
full-size "S"). João's call: every clip with a result line gets the new
look, not only those with a clock. So a render with a result or a clock
needs the new feature, and older workers are not offered it; a render
with neither still goes to any worker, as before. Same mechanism as
every render feature since LIVESTACK-50
(orchestrator/domain/render_features.py).

Everything through the HTTP API of a real orchestrator (the harness),
with the real 2026 Manobrabilidade results (car 26's second run, 66 s).
"""

import pytest

from orchestrator.background.sweepers import expire_leases_once
from orchestrator.domain.render_features import NO_HOLDER_CAN_RENDER_CLOCK_TOP_RIGHT
from orchestrator.testing.scenarios import ask_next, claim
from shared.models import TaskKind
from tests.orchestrator.pass_results_data import set_up_manobrabilidade
from tests.orchestrator.test_pass_clock import (
    FEATURES_0_5_0,
    FEATURES_0_6_0,
    FEATURES_0_7_0,
    ready_job_with_largada,
)
from tests.orchestrator.test_pass_results_render import FEATURES_0_4_0, enroll_master, ready_job


def test_a_0_6_0_worker_is_not_offered_a_render_with_a_clock(harness):
    # Setup: the only holder runs 0.6.0; the job has a clock (largada marked).
    set_up_manobrabilidade(harness)
    master = enroll_master(harness, FEATURES_0_6_0)
    job_id = ready_job_with_largada(harness, master)

    # No offer, and a claim is refused: it would draw the old clock.
    assert ask_next(harness, master, [TaskKind.PROCESS]) is None
    refused = claim(harness, master, job_id, TaskKind.PROCESS)
    assert refused.status_code == 409
    assert "clock_top_right" in refused.json()["error"]["message"]

    # The job says what it waits for: a worker updated to 0.7.0.
    expire_leases_once(harness.context)
    assert harness.job(job_id)["waiting_reason"] == NO_HOLDER_CAN_RENDER_CLOCK_TOP_RIGHT


def test_after_the_update_the_same_worker_gets_the_render(harness):
    set_up_manobrabilidade(harness)
    master = enroll_master(harness, FEATURES_0_6_0)
    job_id = ready_job_with_largada(harness, master)
    expire_leases_once(harness.context)

    # Its next heartbeat (0.7.0) declares the new feature.
    harness.heartbeat(
        master,
        tailnet_address="100.70.0.1",
        render_features=FEATURES_0_7_0,
        held_job_ids=[job_id],
    )
    expire_leases_once(harness.context)

    # The waiting sentence is gone and the render is offered.
    assert harness.job(job_id)["waiting_reason"] is None
    task = ask_next(harness, master, [TaskKind.PROCESS])
    assert task["job_id"] == job_id
    assert task["required_render_features"] == ["pass_result", "clock", "clock_top_right"]


@pytest.mark.parametrize("features", [FEATURES_0_5_0, FEATURES_0_6_0], ids=["0.5.0", "0.6.0"])
def test_a_render_with_a_result_but_no_clock_is_not_offered_to_an_older_worker(harness, features):
    # No largada marked: no clock, but a result line ("66 s · 4 cones →
    # 86 s"). Before João's call a 0.5.0 or 0.6.0 worker took it and drew
    # a full-size "S"; now the result needs the new look too.
    set_up_manobrabilidade(harness)
    master = enroll_master(harness, features)
    job_id = ready_job(harness, master)

    assert ask_next(harness, master, [TaskKind.PROCESS]) is None
    refused = claim(harness, master, job_id, TaskKind.PROCESS)
    assert refused.status_code == 409
    assert "clock_top_right" in refused.json()["error"]["message"]
    expire_leases_once(harness.context)
    assert harness.job(job_id)["waiting_reason"] == NO_HOLDER_CAN_RENDER_CLOCK_TOP_RIGHT


def test_a_render_with_a_result_but_no_clock_goes_to_a_0_7_0_worker(harness):
    set_up_manobrabilidade(harness)
    master = enroll_master(harness, FEATURES_0_7_0)
    job_id = ready_job(harness, master)

    task = ask_next(harness, master, [TaskKind.PROCESS])

    assert task["job_id"] == job_id
    assert (task["pass_clock"], task["pass_result_text"]) == (None, "66 s · 4 cones → 86 s")
    assert task["required_render_features"] == ["pass_result", "clock_top_right"]


@pytest.mark.parametrize("features", [FEATURES_0_4_0, FEATURES_0_6_0], ids=["0.4.0", "0.6.0"])
def test_a_render_with_neither_still_goes_to_any_worker(harness, features):
    # Car 5 did not do its second run: no result line, and no largada
    # marked: nothing of the new look is drawn, so an older worker renders it.
    set_up_manobrabilidade(harness)
    master = enroll_master(harness, features)
    job_id = ready_job(harness, master, car=5, passagem=2)

    task = ask_next(harness, master, [TaskKind.PROCESS])

    assert task["job_id"] == job_id
    assert (task["pass_clock"], task["pass_result_text"]) == (None, None)
    assert task["required_render_features"] == []
