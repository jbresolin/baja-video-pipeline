"""Tests for delivery: studio ACKs (direct push and outbox) and the clip manifest (design §7)."""

import hashlib

import pytest

from orchestrator.testing.scenarios import (
    SAMPLE_CLIP,
    claim_ok,
    hold_raw,
    make_proxy,
    receive_job,
    review_to_ready,
    upload_in_chunks,
)
from shared.models import TaskKind, WorkerRole

CLIP_SHA256 = hashlib.sha256(SAMPLE_CLIP).hexdigest()


@pytest.fixture
def claimed_job(harness):
    """A reviewed job being processed by the master. Returns (master, studio, job id, lease)."""
    master = harness.enroll(WorkerRole.MASTER, "master")
    studio = harness.enroll(WorkerRole.STUDIO, "studio")
    job_id = receive_job(harness)
    hold_raw(harness, master, job_id)
    make_proxy(harness, master, job_id)
    review_to_ready(harness, job_id, prova="Enduro", car_number=42, passagem=3)
    lease_id, _ = claim_ok(harness, master, job_id, TaskKind.PROCESS)
    return master, studio, job_id, lease_id


def ack_direct_push(harness, studio, job_id, sha256=CLIP_SHA256):
    return harness.client.post(
        f"/jobs/{job_id}/delivered",
        json={"sha256": sha256, "size_bytes": len(SAMPLE_CLIP), "relative_path": "enduro/42/3.mp4"},
        headers=studio.headers,
    )


def test_direct_push_ack_takes_a_claimed_job_to_done_and_ends_the_lease(harness, claimed_job):
    master, studio, job_id, lease_id = claimed_job
    response = ack_direct_push(harness, studio, job_id)
    assert response.json() == {"job_id": job_id, "state": "done"}
    job = harness.job(job_id)
    assert job["clip"]["route"] == "direct"
    assert job["clip"]["rendered_by_worker_id"] == master.worker_id
    # The pushing worker sees its lease "lost", which here means "finished".
    answer = harness.heartbeat(master, current_lease_ids=[lease_id], held_job_ids=[job_id])
    assert answer["lease_status"] == {lease_id: "lost"}
    # Repeating the ACK (the studio retries until it gets an answer) is harmless.
    assert ack_direct_push(harness, studio, job_id).json()["state"] == "done"


def test_only_the_studio_may_ack(harness, claimed_job):
    master, _, job_id, _ = claimed_job
    response = ack_direct_push(harness, master, job_id)
    assert response.status_code == 403


def test_outbox_route_ack_with_the_wrong_hash_is_refused(harness, claimed_job):
    master, studio, job_id, lease_id = claimed_job
    upload_in_chunks(harness, master, f"/jobs/{job_id}/clip", lease_id, SAMPLE_CLIP)
    assert harness.job(job_id)["state"] == "uploaded"
    deliver_lease, _ = claim_ok(harness, studio, job_id, TaskKind.DELIVER)
    wrong = harness.client.post(
        f"/outbox/{job_id}/ack",
        json={"lease_id": deliver_lease, "sha256": "0" * 64, "relative_path": "enduro/42/3.mp4"},
        headers=studio.headers,
    )
    assert wrong.status_code == 422
    # The clip stays in the outbox so the studio can download it again.
    assert harness.context.layout.outbox_path(job_id).exists()
    assert harness.job(job_id)["state"] == "uploaded"


def deliver(harness, studio, job_id, sha256=CLIP_SHA256):
    ack_direct_push(harness, studio, job_id, sha256)


def test_manifest_lists_the_latest_clip_per_path_newest_first(harness, claimed_job):
    master, studio, job_id, _ = claimed_job
    deliver(harness, studio, job_id)
    # A second pass video of another car, delivered a minute later.
    harness.clock.advance(60)
    harness.heartbeat(master)
    second_job = receive_job(harness, b"another pass" * 100)
    hold_raw(harness, master, second_job)
    make_proxy(harness, master, second_job)
    review_to_ready(harness, second_job, prova="Aceleração", car_number=7, passagem=1)
    claim_ok(harness, master, second_job, TaskKind.PROCESS)
    harness.client.post(
        f"/jobs/{second_job}/delivered",
        json={"sha256": "1" * 64, "size_bytes": 10, "relative_path": "aceleracao/7/1.mp4"},
        headers=studio.headers,
    )
    clips = harness.client.get("/manifest", auth=harness.reviewer_auth).json()["clips"]
    assert [clip["relative_path"] for clip in clips] == ["aceleracao/7/1.mp4", "enduro/42/3.mp4"]
    # Filter by prova, by name or slug.
    by_slug = harness.client.get("/manifest?prova=aceleracao", auth=harness.reviewer_auth)
    assert [clip["job_id"] for clip in by_slug.json()["clips"]] == [second_job]
    # Only clips delivered after `since`.
    first_delivery = clips[1]["delivered_at"]
    newer = harness.client.get(
        "/manifest", params={"since": first_delivery}, headers=studio.headers
    )
    assert [clip["job_id"] for clip in newer.json()["clips"]] == [second_job]


def test_a_reprocessed_job_leaves_the_manifest_until_delivered_again(harness, claimed_job):
    master, studio, job_id, _ = claimed_job
    deliver(harness, studio, job_id)
    job = harness.job(job_id)
    harness.client.patch(
        f"/jobs/{job_id}/review",
        json={"expected_version": job["version"], "action": "reprocess", "cut_out_s": 6},
        auth=harness.reviewer_auth,
    )
    assert harness.client.get("/manifest", headers=studio.headers).json()["clips"] == []
