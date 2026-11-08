""" "Baixar original" on the job page (LIVESTACK-41 item 15).

A reviewer needs the original when the 480p proxy is too small to judge. The
VPS keeps raws only until two holders have them, so:
  * while the inbox still has the raw, the link works at once (a hard link
    into originals/, which survives the inbox cleanup);
  * otherwise a holder (the master first) is offered `upload_original`,
    uploads the raw, and the link appears;
  * with no holder online, the answer says so;
  * originals are temporary: gone 24 h after the last request, and earlier,
    oldest first, when the folder passes its cap or the disk gets tight;
  * nobody without a session gets anything, and the job itself never
    changes (no state, no failure, the copy is never a holder).

The fake data: `receive_job` sends 10 KiB "video" from "Voluntário Teste";
the harness clock reads 2026-10-25 12:00 UTC (09:00 in São Paulo).
"""

import hashlib

from orchestrator.background.sweepers import clean_inbox_once
from orchestrator.domain.originals import sweep_originals
from orchestrator.testing.scenarios import (
    SAMPLE_VIDEO_BYTES,
    ask_next,
    claim_ok,
    hold_raw,
    receive_job,
    upload_in_chunks,
)
from shared.models import TaskKind, WorkerRole

HOUR = 3600


def ask_for_original(harness, job_id):
    response = harness.client.post(f"/jobs/{job_id}/original", auth=harness.reviewer_auth)
    assert response.status_code == 200, response.text
    return response.json()


def original_status(harness, job_id):
    return harness.client.get(f"/jobs/{job_id}/original", auth=harness.reviewer_auth).json()


def job_held_by_two(harness):
    """A job whose raw the master and a volunteer hold; the VPS copy is gone."""
    master = harness.enroll(WorkerRole.MASTER, "master")
    volunteer = harness.enroll(WorkerRole.WORKER, "volunteer")
    job_id = receive_job(harness, SAMPLE_VIDEO_BYTES)
    hold_raw(harness, master, job_id)
    harness.clock.advance(31)
    harness.heartbeat(master, held_job_ids=[job_id])
    harness.heartbeat(volunteer)
    hold_raw(harness, volunteer, job_id)
    clean_inbox_once(harness.context)
    assert harness.job(job_id)["raw_on_vps"] is False
    return master, volunteer, job_id


def upload_the_original(harness, worker, job_id, data=SAMPLE_VIDEO_BYTES):
    lease_id, task = claim_ok(harness, worker, job_id, TaskKind.UPLOAD_ORIGINAL)
    return upload_in_chunks(harness, worker, f"/jobs/{job_id}/original-upload", lease_id, data)


def test_while_the_inbox_has_the_raw_the_link_works_at_once(harness):
    job_id = receive_job(harness, SAMPLE_VIDEO_BYTES)

    answer = ask_for_original(harness, job_id)

    # Ready straight away, named after the sender and time (item 12).
    name = f"voluntario-teste-20261025-090000-{job_id[:8]}.mp4"
    assert answer["state"] == "ready"
    assert answer["file_name"] == name
    assert answer["download_url"] == f"/original-downloads/{job_id}/{name}"
    original = harness.context.layout.original_path(job_id, ".mp4")
    assert original.read_bytes() == SAMPLE_VIDEO_BYTES
    # A hard link: the inbox cleanup deleting its own name leaves the original.
    harness.context.layout.inbox_path(job_id, ".mp4").unlink()
    assert original.read_bytes() == SAMPLE_VIDEO_BYTES


def test_otherwise_the_master_is_asked_to_upload_it_and_then_the_link_works(harness):
    master, volunteer, job_id = job_held_by_two(harness)

    answer = ask_for_original(harness, job_id)

    assert answer == {
        "state": "preparing",
        "download_url": None,
        "file_name": None,
        "size_bytes": None,
        "expires_at": None,
    }
    # The volunteer holds it too, but the master is asked first...
    assert ask_next(harness, volunteer, [TaskKind.UPLOAD_ORIGINAL]) is None
    offer = ask_next(harness, master, [TaskKind.UPLOAD_ORIGINAL])
    assert offer["kind"] == "upload_original"
    assert offer["upload_url"].endswith(f"/jobs/{job_id}/original-upload")
    # ...it uploads the raw, and the page now gets a link that expires in 24 h.
    assert upload_the_original(harness, master, job_id).json()["complete"] is True
    status = original_status(harness, job_id)
    assert status["state"] == "ready"
    assert status["expires_at"] == "2026-10-26T12:00:31Z"
    # Done: nobody is offered it again.
    assert ask_next(harness, master, [TaskKind.UPLOAD_ORIGINAL]) is None


def test_with_the_master_away_another_holder_uploads_it(harness):
    master, volunteer, job_id = job_held_by_two(harness)
    harness.heartbeat(master, held_job_ids=[job_id], paused=True)
    harness.heartbeat(volunteer, held_job_ids=[job_id])
    ask_for_original(harness, job_id)
    assert ask_next(harness, volunteer, [TaskKind.UPLOAD_ORIGINAL])["job_id"] == job_id


def test_with_no_holder_online_the_page_says_so(harness):
    master, volunteer, job_id = job_held_by_two(harness)
    harness.clock.advance(60)  # both stopped heartbeating

    assert ask_for_original(harness, job_id)["state"] == "no_holder_online"


def test_only_this_jobs_raw_is_accepted_as_its_original(harness):
    master, _, job_id = job_held_by_two(harness)
    ask_for_original(harness, job_id)
    response = upload_the_original(harness, master, job_id, data=b"some other file")
    assert response.status_code == 422
    assert not harness.context.layout.original_path(job_id, ".mp4").exists()


def test_a_failed_upload_never_touches_the_job_and_another_holder_tries(harness):
    master, volunteer, job_id = job_held_by_two(harness)
    ask_for_original(harness, job_id)
    state_before = harness.job(job_id)["state"]
    lease_id, _ = claim_ok(harness, master, job_id, TaskKind.UPLOAD_ORIGINAL)

    harness.client.post(
        f"/jobs/{job_id}/failed",
        json={"lease_id": lease_id, "message": "disk read error", "scope": "worker"},
        headers=master.headers,
    ).raise_for_status()

    job = harness.job(job_id)
    assert job["state"] == state_before and job["failure"] is None
    assert ask_next(harness, volunteer, [TaskKind.UPLOAD_ORIGINAL])["job_id"] == job_id


def test_the_original_is_never_a_holder(harness):
    master, volunteer, job_id = job_held_by_two(harness)
    ask_for_original(harness, job_id)
    upload_the_original(harness, master, job_id)

    job = harness.job(job_id)
    assert sorted(h["worker_id"] for h in job["holders"]) == sorted(
        [master.worker_id, volunteer.worker_id]
    )
    assert job["raw_on_vps"] is False


def test_originals_go_24_hours_after_the_last_request(harness):
    job_id = receive_job(harness, SAMPLE_VIDEO_BYTES)
    ask_for_original(harness, job_id)
    original = harness.context.layout.original_path(job_id, ".mp4")

    harness.clock.advance(23 * HOUR)
    ask_for_original(harness, job_id)  # asked again: another 24 h from now
    harness.clock.advance(23 * HOUR)
    assert sweep_originals(harness.context) == []
    assert original.exists()

    harness.clock.advance(2 * HOUR)
    assert sweep_originals(harness.context) == [job_id]
    assert not original.exists()
    assert original_status(harness, job_id)["state"] == "not_requested"


def test_over_the_size_cap_the_oldest_request_goes_first(harness):
    harness.client.patch(
        "/admin/settings", json={"originals_cap_gb": 0.000015}, auth=harness.admin_auth
    ).raise_for_status()  # 15 kB: room for one 10 KiB original, not two
    first = receive_job(harness, SAMPLE_VIDEO_BYTES)
    ask_for_original(harness, first)
    harness.clock.advance(60)
    second = receive_job(harness, data=SAMPLE_VIDEO_BYTES + b"x")
    ask_for_original(harness, second)

    assert sweep_originals(harness.context) == [first]
    assert original_status(harness, second)["state"] == "ready"


def test_a_tight_disk_also_evicts_originals(harness):
    job_id = receive_job(harness, SAMPLE_VIDEO_BYTES)
    ask_for_original(harness, job_id)
    # Below the proxy eviction threshold (Settings, 10 GB by default).
    harness.context.layout._free_bytes_reader = lambda path: 1_000_000

    assert sweep_originals(harness.context) == [job_id]


def test_without_a_session_nothing_is_given(harness):
    job_id = receive_job(harness, SAMPLE_VIDEO_BYTES)
    assert harness.client.post(f"/jobs/{job_id}/original").status_code == 401
    assert harness.client.get(f"/jobs/{job_id}/original").status_code == 401
    assert not harness.context.layout.original_path(job_id, ".mp4").exists()
    # The file itself goes through Caddy's forward_auth to /auth/media,
    # which answers 401 to a browser without a live session.
    response = harness.client.get(
        "/auth/media",
        headers={"X-Forwarded-Uri": f"/original-downloads/{job_id}/x.mp4"},
    )
    assert response.status_code == 401


def test_the_settings_page_has_both_original_settings(harness):
    settings = harness.client.get("/admin/settings", auth=harness.admin_auth).json()["settings"]
    assert settings["originals_keep_hours"] == 24
    assert settings["originals_cap_gb"] == 10


def test_the_task_carries_the_raws_hash_for_the_upload(harness):
    master, _, job_id = job_held_by_two(harness)
    ask_for_original(harness, job_id)
    offer = ask_next(harness, master, [TaskKind.UPLOAD_ORIGINAL])
    assert offer["raw_sha256"] == hashlib.sha256(SAMPLE_VIDEO_BYTES).hexdigest()
    assert offer["size_bytes"] == len(SAMPLE_VIDEO_BYTES)
