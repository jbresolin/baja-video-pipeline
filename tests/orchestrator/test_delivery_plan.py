"""Tests for the delivery decision carried by process offers (design §17).

The orchestrator, not the worker, decides how a finished clip travels:
"direct" (push to the studio's receiver over the tailnet) when a studio
heartbeated within the last 45 s, else "outbox" (upload to the VPS).
"""

from orchestrator.testing.scenarios import (
    ask_next,
    claim_ok,
    hold_raw,
    make_proxy,
    receive_job,
    review_to_ready,
)
from shared.models import TaskKind, WorkerRole


def ready_job_held_by_master(harness):
    """Enroll a master and bring one job to "ready" with the master as holder."""
    master = harness.enroll(WorkerRole.MASTER, "master")
    job_id = receive_job(harness)
    hold_raw(harness, master, job_id)
    make_proxy(harness, master, job_id)
    review_to_ready(harness, job_id)
    return master, job_id


def test_without_a_studio_the_offer_says_outbox(harness):
    master, job_id = ready_job_held_by_master(harness)
    offer = ask_next(harness, master, [TaskKind.PROCESS])
    # No studio is enrolled at all, so the only way is the VPS outbox.
    assert offer["delivery"] == {"mode": "outbox", "studio_url": None}


def test_an_online_studio_gets_direct_pushes_at_its_receiver(harness):
    master, job_id = ready_job_held_by_master(harness)
    studio = harness.enroll(WorkerRole.STUDIO, "streaming-pc", heartbeat=False)
    # The studio's heartbeat tells where its receiver listens.
    harness.heartbeat(studio, tailnet_address="100.80.0.5", receiver_port=8720)
    offer = ask_next(harness, master, [TaskKind.PROCESS])
    assert offer["delivery"] == {
        "mode": "direct",
        "studio_url": f"http://100.80.0.5:8720/receive/{job_id}",
    }
    # The claim answer repeats the task with the same decision.
    _, claimed_task = claim_ok(harness, master, job_id, TaskKind.PROCESS)
    assert claimed_task["delivery"]["mode"] == "direct"


def test_a_studio_silent_for_more_than_45_seconds_means_outbox(harness):
    master, job_id = ready_job_held_by_master(harness)
    studio = harness.enroll(WorkerRole.STUDIO, "streaming-pc", heartbeat=False)
    harness.heartbeat(studio, tailnet_address="100.80.0.5")
    # 46 s pass; only the master keeps sending heartbeats.
    harness.clock.advance(46)
    harness.heartbeat(master, held_job_ids=[job_id])
    offer = ask_next(harness, master, [TaskKind.PROCESS])
    assert offer["delivery"]["mode"] == "outbox"


def test_a_studio_that_reported_no_address_cannot_be_pushed_to(harness):
    # Online, but without a tailnet address there is nowhere to push.
    master, job_id = ready_job_held_by_master(harness)
    harness.enroll(WorkerRole.STUDIO, "streaming-pc")
    offer = ask_next(harness, master, [TaskKind.PROCESS])
    assert offer["delivery"]["mode"] == "outbox"


def test_the_default_receiver_port_is_used_when_none_was_reported(harness):
    master, job_id = ready_job_held_by_master(harness)
    studio = harness.enroll(WorkerRole.STUDIO, "streaming-pc", heartbeat=False)
    harness.heartbeat(studio, tailnet_address="studio.tailnet.ts.net")
    offer = ask_next(harness, master, [TaskKind.PROCESS])
    # 8720 is the studio receiver's contract port (design §8).
    assert offer["delivery"]["studio_url"] == f"http://studio.tailnet.ts.net:8720/receive/{job_id}"
