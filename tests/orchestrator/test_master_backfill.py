"""Tests for the master's backfill offers (design §17).

The master must end up holding every raw that was not discarded, also the
ones that reached two holders and finished while it was offline. There is
no special endpoint: /next simply keeps offering the master download_raw
for those jobs, but only after everything else it could do ("lowest
priority after live work").
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

CLIP_SHA = "c" * 64


def finish_job_without_the_master(harness, first, second, studio) -> str:
    """A job held by two volunteers and delivered straight to the studio: state done.

    The master is offline the whole time (the caller made sure of that), so
    it never got a copy.
    """
    job_id = receive_job(harness)
    hold_raw(harness, first, job_id)
    hold_raw(harness, second, job_id)
    make_proxy(harness, first, job_id)
    review_to_ready(harness, job_id)
    claim_ok(harness, first, job_id, TaskKind.PROCESS)
    delivered = harness.client.post(
        f"/jobs/{job_id}/delivered",
        json={"sha256": CLIP_SHA, "size_bytes": 10, "relative_path": "enduro/42/1.mp4"},
        headers=studio.headers,
    )
    assert delivered.json()["state"] == "done"
    return job_id


def enroll_fleet_with_master_offline(harness):
    """Master, two volunteers with tailnet addresses and a studio; then the master goes quiet."""
    master = harness.enroll(WorkerRole.MASTER, "master")
    first = harness.enroll(WorkerRole.WORKER, "first", tailnet_address="100.70.0.1")
    second = harness.enroll(WorkerRole.WORKER, "second", tailnet_address="100.70.0.2")
    studio = harness.enroll(WorkerRole.STUDIO, "studio")
    harness.clock.advance(50)  # 50 s without a heartbeat: the master is offline
    harness.heartbeat(first, tailnet_address="100.70.0.1")
    harness.heartbeat(second, tailnet_address="100.70.0.2")
    harness.heartbeat(studio)
    return master, first, second, studio


def test_the_returning_master_is_offered_a_done_job_it_does_not_hold(harness):
    master, first, second, studio = enroll_fleet_with_master_offline(harness)
    job_id = finish_job_without_the_master(harness, first, second, studio)
    # Two volunteers confirmed, so the VPS deleted its copy; the only
    # sources left are the volunteers' file servers (port 8710).
    assert harness.job(job_id)["raw_on_vps"] is False

    harness.heartbeat(master)
    harness.heartbeat(first, held_job_ids=[job_id], tailnet_address="100.70.0.1")
    harness.heartbeat(second, held_job_ids=[job_id], tailnet_address="100.70.0.2")
    offer = ask_next(harness, master, [TaskKind.DOWNLOAD_RAW])
    assert offer["kind"] == "download_raw" and offer["job_id"] == job_id
    # Both volunteers are sources (which one comes first does not matter).
    sources = {offer["download_url"], *offer["fallback_download_urls"]}
    assert sources == {
        f"http://100.70.0.1:8710/raw/{job_id}",
        f"http://100.70.0.2:8710/raw/{job_id}",
    }
    # Once it confirms, the master holds a done job: nothing more to offer.
    hold_raw(harness, master, job_id)
    assert ask_next(harness, master, [TaskKind.DOWNLOAD_RAW]) is None


def test_volunteers_are_never_offered_a_finished_job(harness):
    # Backfill is the master's job only: a third volunteer that does not
    # hold the done job gets nothing for it.
    master, first, second, studio = enroll_fleet_with_master_offline(harness)
    finish_job_without_the_master(harness, first, second, studio)
    third = harness.enroll(WorkerRole.WORKER, "third")
    assert ask_next(harness, third, [TaskKind.DOWNLOAD_RAW]) is None


def test_failed_jobs_are_backfilled_but_discarded_ones_never(harness):
    # Two jobs reach the inbox while the master is offline. One fails
    # (scope job), the other is discarded by a reviewer.
    master, first, second, _ = enroll_fleet_with_master_offline(harness)
    failed_id = receive_job(harness)
    lease_id, _ = claim_ok(harness, first, failed_id, TaskKind.DOWNLOAD_RAW)
    harness.client.post(
        f"/jobs/{failed_id}/failed",
        json={"lease_id": lease_id, "message": "moov atom not found", "scope": "job"},
        headers=first.headers,
    )
    discarded_id = receive_job(harness)
    hold_raw(harness, second, discarded_id)
    make_proxy(harness, second, discarded_id)
    version = harness.job(discarded_id)["version"]
    harness.client.patch(
        f"/jobs/{discarded_id}/review",
        json={"expected_version": version, "action": "discard"},
        auth=harness.reviewer_auth,
    )

    harness.heartbeat(master)
    # The failed job still has its inbox copy, so the master fetches it from there.
    offer = ask_next(harness, master, [TaskKind.DOWNLOAD_RAW])
    assert offer["job_id"] == failed_id
    assert offer["download_url"].endswith(f"/inbox/{failed_id}.mp4")
    hold_raw(harness, master, failed_id)
    # The discarded job is never offered: its raw may be deleted everywhere.
    assert ask_next(harness, master, [TaskKind.DOWNLOAD_RAW]) is None
    # Holding the failed job's raw did not change what the reviewer sees.
    failed = harness.job(failed_id)
    assert failed["state"] == "failed"
    assert failed["failure"]["message"] == "moov atom not found"


def test_live_work_comes_before_backfill(harness):
    # An old done job the master never got, and a brand-new video that just
    # arrived. Even though the done job is older, the master must first
    # take the new video (live work) and only then backfill.
    master, first, second, studio = enroll_fleet_with_master_offline(harness)
    done_id = finish_job_without_the_master(harness, first, second, studio)
    harness.clock.advance(1)
    new_id = receive_job(harness)

    harness.heartbeat(master)
    harness.heartbeat(first, held_job_ids=[done_id], tailnet_address="100.70.0.1")
    harness.heartbeat(second, held_job_ids=[done_id], tailnet_address="100.70.0.2")
    first_offer = ask_next(harness, master, [TaskKind.DOWNLOAD_RAW])
    assert first_offer["job_id"] == new_id
    hold_raw(harness, master, new_id)

    # The new job now needs a proxy, which the master (a holder) can make.
    # Proxies are live work too, so they also come before the backfill.
    kinds = [TaskKind.DOWNLOAD_RAW, TaskKind.MAKE_PROXY]
    assert ask_next(harness, master, kinds)["kind"] == "make_proxy"
    make_proxy(harness, master, new_id)
    # With no live work left, the backfill finally shows up.
    assert ask_next(harness, master, kinds)["job_id"] == done_id


def test_a_failed_backfill_download_leaves_the_done_job_alone(harness):
    # The master's backfill download of a done job fails (network). The
    # job is finished; its failure fields and waiting reason must stay
    # empty, so the review UI does not suddenly show a problem.
    master, first, second, studio = enroll_fleet_with_master_offline(harness)
    job_id = finish_job_without_the_master(harness, first, second, studio)
    harness.heartbeat(master)
    harness.heartbeat(first, held_job_ids=[job_id], tailnet_address="100.70.0.1")
    lease_id, _ = claim_ok(harness, master, job_id, TaskKind.DOWNLOAD_RAW)
    response = harness.client.post(
        f"/jobs/{job_id}/failed",
        json={"lease_id": lease_id, "message": "peer unreachable"},
        headers=master.headers,
    )
    assert response.json()["state"] == "done"
    job = harness.job(job_id)
    assert job["failure"] is None and job["waiting_reason"] is None
    # The master is not offered the same backfill again (it is excluded).
    assert ask_next(harness, master, [TaskKind.DOWNLOAD_RAW]) is None
