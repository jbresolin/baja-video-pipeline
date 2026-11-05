"""Tests for the clip archive (LIVESTACK-38 item 7; design §11 as amended).

Processed clips are the archive now: the master keeps a copy of *every*
finished clip, not only the ones it rendered, and every version of a
reprocessed pass. These tests drive the orchestrator through the HTTP API:

* a volunteer renders while the master is offline; when the master comes
  back it is offered archive_clip for each clip, from the volunteer's
  file server (:8710/clip/...);
* a reprocess makes version 2; both versions are archived side by side;
* the outbox copy goes only once the studio ACKed *and* the master
  archived it (or 72 h after the ACK if the master never shows up);
* only archived clips are reported back as evictable.
"""

import hashlib

import pytest

from orchestrator.background.sweepers import clean_outbox_once
from orchestrator.testing.scenarios import (
    SAMPLE_CLIP,
    ask_next,
    claim_ok,
    hold_raw,
    make_proxy,
    receive_job,
    review_to_ready,
    upload_in_chunks,
)
from shared.models import TaskKind, WorkerRole

CLIP_SHA256 = hashlib.sha256(SAMPLE_CLIP).hexdigest()
# Past the 45 s online window: a worker that sent nothing for this long is offline.
OFFLINE_SECONDS = 60
HOURS = 3600


@pytest.fixture
def fleet(harness):
    """Master, one volunteer and the studio, each with a tailnet address."""
    master = harness.enroll(WorkerRole.MASTER, "master", tailnet_address="100.70.0.1")
    volunteer = harness.enroll(WorkerRole.WORKER, "volunteer", tailnet_address="100.70.0.2")
    studio = harness.enroll(WorkerRole.STUDIO, "studio", tailnet_address="100.70.0.3")
    return master, volunteer, studio


def ready_job(harness, holder, **review):
    """A job held by `holder`, reviewed and ready to process."""
    job_id = receive_job(harness)
    hold_raw(harness, holder, job_id)
    make_proxy(harness, holder, job_id)
    review_to_ready(harness, job_id, **review)
    return job_id


def render_to_studio(harness, renderer, studio, job_id, clip=SAMPLE_CLIP):
    """`renderer` processes the job and pushes it straight to the studio, which ACKs."""
    claim_ok(harness, renderer, job_id, TaskKind.PROCESS)
    job = harness.job(job_id)
    path = f"{job['prova'].lower()}/{job['car_number']}/{job['passagem']}.mp4"
    response = harness.client.post(
        f"/jobs/{job_id}/delivered",
        json={
            "sha256": hashlib.sha256(clip).hexdigest(),
            "size_bytes": len(clip),
            "relative_path": path,
        },
        headers=studio.headers,
    )
    assert response.status_code == 200, response.text


def archive(harness, master, job_id, sha256):
    """The master claims archive_clip on the job and confirms the copy."""
    lease_id, task = claim_ok(harness, master, job_id, TaskKind.ARCHIVE_CLIP)
    response = harness.client.post(
        f"/jobs/{job_id}/clip-archived",
        json={"lease_id": lease_id, "sha256": sha256},
        headers=master.headers,
    )
    assert response.status_code == 200, response.text
    return task


def test_master_offline_during_rendering_backfills_every_clip(harness, fleet):
    master, volunteer, studio = fleet
    # Two passes, held and rendered by the volunteer (the master holds
    # them too, but it goes offline before any processing).
    first = ready_job(harness, volunteer, car_number=7)
    second = ready_job(harness, volunteer, car_number=8)
    harness.clock.advance(OFFLINE_SECONDS)  # the master stops heartbeating
    # (A heartbeat lists the raws the worker holds; leaving one out would
    # tell the orchestrator it was deleted.)
    harness.heartbeat(volunteer, held_job_ids=[first, second])
    harness.heartbeat(studio)
    render_to_studio(harness, volunteer, studio, first, clip=b"clip-one" * 100)
    harness.clock.advance(1)  # rendered one second apart: "oldest first" is well defined
    render_to_studio(harness, volunteer, studio, second, clip=b"clip-two" * 100)
    # The volunteer keeps both clips and says so in its heartbeat.
    held = [
        {"job_id": first, "sha256": hashlib.sha256(b"clip-one" * 100).hexdigest()},
        {"job_id": second, "sha256": hashlib.sha256(b"clip-two" * 100).hexdigest()},
    ]
    answer = harness.heartbeat(volunteer, held_job_ids=[first, second], held_clips=held)
    # Nothing is archived yet, so the volunteer may evict nothing.
    assert answer["archived_clip_sha256s"] == []
    fleet_view = harness.client.get("/fleet", auth=harness.reviewer_auth).json()
    assert fleet_view["clip_archive"] == {"archived": 0, "total": 2}

    # The master comes back. Its /next offers archive_clip for each clip,
    # oldest first, from the volunteer's file server.
    harness.heartbeat(master, held_job_ids=[first, second])
    for job_id, clip in ((first, held[0]), (second, held[1])):
        offer = ask_next(harness, master, [TaskKind.ARCHIVE_CLIP])
        assert offer["job_id"] == job_id
        assert offer["download_urls"] == [
            f"http://100.70.0.2:8710/clip/{job_id}?sha256={clip['sha256']}"
        ]
        archive(harness, master, job_id, clip["sha256"])

    # Everything archived: no more offers, "2 of 2", and the volunteer is
    # told both clips may now be evicted if it runs short of space.
    assert ask_next(harness, master, [TaskKind.ARCHIVE_CLIP]) is None
    fleet_view = harness.client.get("/fleet", auth=harness.reviewer_auth).json()
    assert fleet_view["clip_archive"] == {"archived": 2, "total": 2}
    answer = harness.heartbeat(volunteer, held_job_ids=[first, second], held_clips=held)
    assert answer["archived_clip_sha256s"] == sorted(clip["sha256"] for clip in held)
    volunteer_row = next(w for w in fleet_view["workers"] if w["worker"]["name"] == "volunteer")
    assert (volunteer_row["clips_held"], volunteer_row["clips_archived"]) == (2, 2)


def test_reprocess_makes_version_two_and_both_are_archived(harness, fleet):
    master, _, studio = fleet
    job_id = ready_job(harness, master, prova="Enduro", car_number=42, passagem=3)
    v1, v2 = b"first-cut" * 100, b"second-cut" * 100
    render_to_studio(harness, master, studio, job_id, clip=v1)
    harness.heartbeat(
        master,
        held_job_ids=[job_id],
        held_clips=[{"job_id": job_id, "sha256": hashlib.sha256(v1).hexdigest()}],
    )
    task = archive(harness, master, job_id, hashlib.sha256(v1).hexdigest())
    # The master held v1 itself, so the task names no download source.
    assert task["download_urls"] == [] and task["archive_relative_path"] == "enduro/42/3.mp4"

    # The reviewer changes the cut: reprocess, render again → version 2.
    job = harness.job(job_id)
    harness.client.patch(
        f"/jobs/{job_id}/review",
        json={"expected_version": job["version"], "action": "reprocess", "cut_out_s": 6},
        auth=harness.reviewer_auth,
    ).raise_for_status()
    render_to_studio(harness, master, studio, job_id, clip=v2)
    harness.heartbeat(
        master,
        held_job_ids=[job_id],
        held_clips=[{"job_id": job_id, "sha256": hashlib.sha256(v2).hexdigest()}],
    )
    task = archive(harness, master, job_id, hashlib.sha256(v2).hexdigest())
    # v2 goes next to v1, never over it.
    assert (task["version"], task["archive_relative_path"]) == (2, "enduro/42/3.v2.mp4")

    job = harness.job(job_id)
    assert [(v["version"], v["archived_at"] is not None) for v in job["clip_versions"]] == [
        (1, True),
        (2, True),
    ]
    # The box and the manifest only have the current one: version 2.
    assert job["clip"]["version"] == 2
    manifest = harness.client.get("/manifest", auth=harness.reviewer_auth).json()
    assert [entry["version"] for entry in manifest["clips"]] == [2]


def outbox_delivery(harness, fleet):
    """A clip uploaded to the outbox by the volunteer (studio offline at the time)."""
    master, volunteer, studio = fleet
    job_id = ready_job(harness, volunteer)
    lease_id, _ = claim_ok(harness, volunteer, job_id, TaskKind.PROCESS)
    upload_in_chunks(harness, volunteer, f"/jobs/{job_id}/clip", lease_id, SAMPLE_CLIP)
    return job_id


def studio_pulls(harness, studio, job_id):
    lease_id, task = claim_ok(harness, studio, job_id, TaskKind.DELIVER)
    harness.client.post(
        f"/outbox/{job_id}/ack",
        json={
            "lease_id": lease_id,
            "sha256": CLIP_SHA256,
            "relative_path": task["clip_relative_path"],
        },
        headers=studio.headers,
    ).raise_for_status()


def test_outbox_copy_waits_for_both_the_studio_ack_and_the_archive(harness, fleet):
    master, _, studio = fleet
    job_id = outbox_delivery(harness, fleet)
    outbox_file = harness.context.layout.outbox_path(job_id)
    # The job page shows the clip as being in the outbox.
    assert harness.job(job_id)["clip"]["in_outbox"] is True

    # The master archives first, straight from the outbox: still kept,
    # because the studio has not taken it yet.
    offer = ask_next(harness, master, [TaskKind.ARCHIVE_CLIP])
    assert offer["download_urls"] == [f"http://100.64.0.1:8080/outbox/{job_id}.mp4"]
    archive(harness, master, job_id, CLIP_SHA256)
    clean_outbox_once(harness.context)
    assert outbox_file.exists()

    # The studio pulls and ACKs: now both have it, and the copy goes.
    studio_pulls(harness, studio, job_id)
    assert not outbox_file.exists()
    assert harness.job(job_id)["clip"]["in_outbox"] is False


def test_an_offline_master_keeps_the_outbox_copy_for_72_hours(harness, fleet):
    master, volunteer, studio = fleet
    job_id = outbox_delivery(harness, fleet)
    outbox_file = harness.context.layout.outbox_path(job_id)
    studio_pulls(harness, studio, job_id)
    # ACKed but not archived (the master never asked): kept, day after day...
    harness.clock.advance(71 * HOURS)
    clean_outbox_once(harness.context)
    assert outbox_file.exists()
    # ...until the 72 h grace period is over; then the studio's copy and
    # the volunteer's own copy have to do.
    harness.clock.advance(2 * HOURS)
    clean_outbox_once(harness.context)
    assert not outbox_file.exists()


def test_the_master_may_download_an_outbox_clip_only_under_its_archive_lease(harness, fleet):
    master, volunteer, _ = fleet
    job_id = outbox_delivery(harness, fleet)
    path = f"/outbox/{job_id}.mp4"

    def caddy_asks(worker):
        return harness.client.get(
            "/auth/file", headers={**worker.headers, "X-Forwarded-Uri": path}
        ).status_code

    # Without a lease: refused, like any non-studio worker.
    assert caddy_asks(master) == 403
    claim_ok(harness, master, job_id, TaskKind.ARCHIVE_CLIP)
    assert caddy_asks(master) == 204
    # A volunteer never may.
    assert caddy_asks(volunteer) == 403


def test_only_the_master_archives(harness, fleet):
    master, volunteer, studio = fleet
    job_id = outbox_delivery(harness, fleet)
    # No archive_clip offers or claims for a volunteer...
    assert ask_next(harness, volunteer, [TaskKind.ARCHIVE_CLIP]) is None
    refused = harness.client.post(
        f"/jobs/{job_id}/claim", json={"task_kind": "archive_clip"}, headers=volunteer.headers
    )
    assert refused.status_code == 409
    # ...and a confirmation with a sha256 that is no version of the job is refused.
    lease_id, _ = claim_ok(harness, master, job_id, TaskKind.ARCHIVE_CLIP)
    wrong = harness.client.post(
        f"/jobs/{job_id}/clip-archived",
        json={"lease_id": lease_id, "sha256": "0" * 64},
        headers=master.headers,
    )
    assert wrong.status_code == 422


def test_a_failed_archive_copy_is_retried_later_without_blocking_others(harness, fleet):
    master, volunteer, studio = fleet
    job_id = outbox_delivery(harness, fleet)
    lease_id, _ = claim_ok(harness, master, job_id, TaskKind.ARCHIVE_CLIP)
    # The master reports that copying failed (its disk was full, say).
    harness.client.post(
        f"/jobs/{job_id}/failed",
        json={"lease_id": lease_id, "message": "disk full", "scope": "worker"},
        headers=master.headers,
    ).raise_for_status()
    # The job itself is untouched: archiving is bookkeeping, not the pipeline.
    job = harness.job(job_id)
    assert job["state"] == "uploaded" and job["failure"] is None
    # For ten minutes this clip is not offered again...
    assert ask_next(harness, master, [TaskKind.ARCHIVE_CLIP]) is None
    # ...then it is (no permanent exclusion: only the master can archive).
    harness.clock.advance(11 * 60)
    for worker in (master, volunteer, studio):
        harness.heartbeat(worker, held_job_ids=[job_id] if worker is volunteer else [])
    assert ask_next(harness, master, [TaskKind.ARCHIVE_CLIP])["job_id"] == job_id


def test_the_72_hours_are_an_admin_setting(harness, fleet):
    # Same situation as above, but the admin shortened the wait to 6 h on
    # the Settings page: the change applies at once, no restart.
    _, _, studio = fleet
    response = harness.client.patch(
        "/admin/settings", json={"outbox_keep_unarchived_hours": 6}, auth=harness.admin_auth
    )
    assert response.status_code == 200, response.text
    job_id = outbox_delivery(harness, fleet)
    studio_pulls(harness, studio, job_id)

    harness.clock.advance(7 * HOURS)
    clean_outbox_once(harness.context)
    assert not harness.context.layout.outbox_path(job_id).exists()


def test_a_revoked_worker_is_no_longer_a_clip_source(harness, fleet):
    # The volunteer rendered a clip and keeps it; the master is offline.
    master, volunteer, studio = fleet
    job_id = ready_job(harness, volunteer)
    harness.clock.advance(OFFLINE_SECONDS)
    harness.heartbeat(volunteer, held_job_ids=[job_id])
    harness.heartbeat(studio)
    render_to_studio(harness, volunteer, studio, job_id)
    held = [{"job_id": job_id, "sha256": CLIP_SHA256}]
    harness.heartbeat(volunteer, held_job_ids=[job_id], held_clips=held)

    # An admin revokes the volunteer (admin Workers page).
    response = harness.client.post(
        f"/admin/workers/{volunteer.worker_id}/revoke", auth=harness.admin_auth
    )
    assert response.status_code == 200, response.text

    # Its kept clip is forgotten: the fleet page counts nothing for it, and
    # the master, back online, is not sent to download from it.
    fleet_view = harness.client.get("/fleet", auth=harness.admin_auth).json()
    row = next(w for w in fleet_view["workers"] if w["worker"]["name"] == "volunteer")
    assert row["clips_held"] == 0
    harness.heartbeat(master, held_job_ids=[job_id])
    assert ask_next(harness, master, [TaskKind.ARCHIVE_CLIP]) is None
