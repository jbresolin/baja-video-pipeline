""" "Baixar clipe": download the full clip on demand (LIVESTACK-52).

The same request pattern as "Baixar original":
  * while the VPS outbox still has the clip, the link works at once;
  * otherwise a PC that has the clip uploads it: the master (it archives
    every clip) first, then the worker that rendered it, the studio only
    as a last resort and only while it is not delivering; only PCs whose
    version can do it (installer 0.7.0) are asked;
  * the download is named after the clip ("enduro-042-p1.mp4");
  * the VPS copy goes 30 minutes after the first *complete* download, or
    24 h after the request if nobody downloads it, and earlier when the
    disk is tight; a download that breaks off does not count;
  * nobody without a session gets anything.

Each test builds a fresh orchestrator (the `harness` fixture) whose clock
starts at 2026-10-25 12:00 UTC and moves only when a test advances it.
"""

from orchestrator.domain.requested_clips import requested_clip_chunks, sweep_requested_clips
from orchestrator.testing.harness import REVIEWER_NAME, REVIEWER_PASSWORD
from orchestrator.testing.scenarios import (
    SAMPLE_CLIP,
    ask_next,
    claim_ok,
    upload_in_chunks,
)
from shared.models import TaskKind, WorkerRole
from tests.orchestrator.clip_review_helpers import (
    HOUR,
    MINUTE,
    OFFLINE_SECONDS,
    beat,
    enroll_new,
    master_volunteer_studio,
    ready_job,
    render_to_outbox,
    render_to_studio,
    reprocess,
    sha256_of,
)

CLIP_SHA256 = sha256_of(SAMPLE_CLIP)
# Prova "Enduro", car 42, passagem 1 (review_to_ready's defaults).
CLIP_NAME = "enduro-042-p1.mp4"


def ask_for_clip(harness, job_id):
    response = harness.client.post(f"/jobs/{job_id}/clip-download", auth=harness.reviewer_auth)
    assert response.status_code == 200, response.text
    return response.json()


def clip_status(harness, job_id):
    return harness.client.get(f"/jobs/{job_id}/clip-download", auth=harness.reviewer_auth).json()


def browser_cookie(harness) -> str:
    """The review UI's session cookie, as a signed-in browser sends it."""
    login = harness.client.post(
        "/auth/login", json={"username": REVIEWER_NAME, "password": REVIEWER_PASSWORD}
    )
    return f"bvp_session={login.json()['session_token']}"


def download(harness, url, cookie, **headers):
    return harness.client.get(url, headers={"Cookie": cookie, **headers})


def delivered_to_studio(harness):
    """A job whose clip the volunteer rendered and pushed to the studio
    (so the outbox never had it); the volunteer keeps the clip, as every
    worker does after a delivery, and says so in its heartbeat."""
    master, volunteer, studio = master_volunteer_studio(harness)
    job_id = ready_job(harness, volunteer)
    render_to_studio(harness, volunteer, studio, job_id)
    beat(harness, volunteer, held_clips=[{"job_id": job_id, "sha256": CLIP_SHA256}])
    return master, volunteer, studio, job_id


def archive_on_master(harness, master, job_id):
    """The master copies the clip into its archive and confirms it."""
    lease_id, _ = claim_ok(harness, master, job_id, TaskKind.ARCHIVE_CLIP)
    harness.client.post(
        f"/jobs/{job_id}/clip-archived",
        json={"lease_id": lease_id, "sha256": CLIP_SHA256},
        headers=master.headers,
    ).raise_for_status()


def upload_the_clip(harness, worker, job_id, data=SAMPLE_CLIP):
    lease_id, _ = claim_ok(harness, worker, job_id, TaskKind.UPLOAD_CLIP)
    return upload_in_chunks(harness, worker, f"/jobs/{job_id}/clip-download-upload", lease_id, data)


def test_while_the_outbox_has_the_clip_the_link_works_at_once(harness):
    # Rendered through the outbox; the studio has not fetched it yet.
    volunteer = enroll_new(harness, WorkerRole.WORKER, "volunteer", "100.70.0.2")
    job_id = ready_job(harness, volunteer)
    render_to_outbox(harness, volunteer, job_id)

    answer = ask_for_clip(harness, job_id)

    assert answer["state"] == "ready"
    assert answer["file_name"] == CLIP_NAME
    assert answer["download_url"] == f"/requested-clips/{job_id}/{CLIP_NAME}"
    assert answer["version"] == 1
    # Never downloaded yet: it would go 24 h after the request.
    assert answer["expires_at"] == "2026-10-26T12:00:00Z"
    # A hard link: the outbox cleanup deleting its own copy leaves this one.
    harness.context.layout.outbox_path(job_id).unlink()
    assert harness.context.layout.requested_clip_path(job_id).read_bytes() == SAMPLE_CLIP


def test_otherwise_the_master_uploads_it_first_and_then_the_link_works(harness):
    master, volunteer, _, job_id = delivered_to_studio(harness)
    archive_on_master(harness, master, job_id)

    answer = ask_for_clip(harness, job_id)

    assert answer["state"] == "preparing"
    # The volunteer that rendered it keeps it too, but the master comes first.
    assert ask_next(harness, volunteer, [TaskKind.UPLOAD_CLIP]) is None
    offer = ask_next(harness, master, [TaskKind.UPLOAD_CLIP])
    assert offer["kind"] == "upload_clip"
    assert (offer["sha256"], offer["version"]) == (CLIP_SHA256, 1)
    # Where the master finds it in its archive, and the studio in its folder.
    assert offer["archive_relative_path"] == "enduro/42/enduro-042-p1.mp4"
    assert offer["studio_relative_path"] == "enduro/42/enduro-042-p1.mp4"
    assert offer["upload_url"].endswith(f"/jobs/{job_id}/clip-download-upload")

    assert upload_the_clip(harness, master, job_id).json()["complete"] is True

    assert clip_status(harness, job_id)["state"] == "ready"
    # Done: nobody is asked again.
    assert ask_next(harness, master, [TaskKind.UPLOAD_CLIP]) is None


def test_the_renderer_uploads_it_when_the_master_does_not_have_it(harness):
    # The master never archived this clip (it was offline the whole time).
    master, volunteer, _, job_id = delivered_to_studio(harness)
    harness.clock.advance(OFFLINE_SECONDS)
    beat(harness, volunteer, held_clips=[{"job_id": job_id, "sha256": CLIP_SHA256}])

    ask_for_clip(harness, job_id)

    assert ask_next(harness, volunteer, [TaskKind.UPLOAD_CLIP])["job_id"] == job_id


def test_a_master_online_without_the_clip_does_not_hold_up_the_renderer(harness):
    # The master is online but has not archived the clip yet.
    master, volunteer, _, job_id = delivered_to_studio(harness)
    ask_for_clip(harness, job_id)

    assert ask_next(harness, master, [TaskKind.UPLOAD_CLIP]) is None
    assert ask_next(harness, volunteer, [TaskKind.UPLOAD_CLIP])["job_id"] == job_id


def test_the_studio_is_asked_only_as_a_last_resort(harness):
    master, volunteer, studio, job_id = delivered_to_studio(harness)
    ask_for_clip(harness, job_id)
    # While the renderer is online, the studio is left alone.
    assert ask_next(harness, studio, [TaskKind.UPLOAD_CLIP]) is None

    # Everyone but the studio goes offline (the studio keeps heartbeating).
    harness.clock.advance(OFFLINE_SECONDS)
    beat(harness, studio)

    offer = ask_next(harness, studio, [TaskKind.UPLOAD_CLIP])
    assert offer["job_id"] == job_id
    assert upload_the_clip(harness, studio, job_id).json()["complete"] is True


def test_the_studio_is_not_asked_while_it_is_delivering(harness):
    master, volunteer, studio, job_id = delivered_to_studio(harness)
    ask_for_clip(harness, job_id)
    # Another clip is on its way to the studio: it waits in the outbox.
    other = ready_job(harness, volunteer, car_number=7)
    render_to_outbox(harness, volunteer, other, clip=SAMPLE_CLIP + b"-other")
    harness.clock.advance(OFFLINE_SECONDS)
    beat(harness, studio)

    # Delivering comes first; the page says no PC can send the clip now.
    assert ask_next(harness, studio, [TaskKind.UPLOAD_CLIP]) is None
    assert clip_status(harness, job_id)["state"] == "no_source_online"


def test_pcs_before_0_7_0_are_never_asked(harness):
    # The renderer runs an older installer: it declared no upload_clip.
    master, volunteer, _, job_id = delivered_to_studio(harness)
    beat(harness, volunteer, features=[], held_clips=[{"job_id": job_id, "sha256": CLIP_SHA256}])
    harness.clock.advance(OFFLINE_SECONDS)
    beat(harness, volunteer, features=[], held_clips=[{"job_id": job_id, "sha256": CLIP_SHA256}])

    assert ask_for_clip(harness, job_id)["state"] == "no_source_online"
    assert ask_next(harness, volunteer, [TaskKind.UPLOAD_CLIP]) is None


def test_with_nobody_online_the_page_says_so(harness):
    _, _, _, job_id = delivered_to_studio(harness)
    harness.clock.advance(OFFLINE_SECONDS)
    assert ask_for_clip(harness, job_id)["state"] == "no_source_online"


def test_a_failed_upload_passes_the_task_to_the_next_pc(harness):
    master, volunteer, _, job_id = delivered_to_studio(harness)
    archive_on_master(harness, master, job_id)
    ask_for_clip(harness, job_id)
    lease_id, _ = claim_ok(harness, master, job_id, TaskKind.UPLOAD_CLIP)

    # The master cannot find its copy (the worker reports scope "worker").
    harness.client.post(
        f"/jobs/{job_id}/failed",
        json={"lease_id": lease_id, "message": "not on this PC", "scope": "worker"},
        headers=master.headers,
    ).raise_for_status()

    # The job itself is untouched, and the renderer is asked instead.
    job = harness.job(job_id)
    assert job["state"] == "done" and job["failure"] is None
    assert ask_next(harness, volunteer, [TaskKind.UPLOAD_CLIP])["job_id"] == job_id


def test_only_the_requested_clip_is_accepted(harness):
    master, _, _, job_id = delivered_to_studio(harness)
    archive_on_master(harness, master, job_id)
    ask_for_clip(harness, job_id)

    response = upload_the_clip(harness, master, job_id, data=b"some other file")

    assert response.status_code == 422
    assert not harness.context.layout.requested_clip_path(job_id).exists()


def test_the_download_is_the_clip_under_its_readable_name(harness):
    volunteer = enroll_new(harness, WorkerRole.WORKER, "volunteer", "100.70.0.2")
    job_id = ready_job(harness, volunteer)
    render_to_outbox(harness, volunteer, job_id)
    url = ask_for_clip(harness, job_id)["download_url"]

    response = download(harness, url, browser_cookie(harness))

    assert response.status_code == 200
    assert response.content == SAMPLE_CLIP
    assert response.headers["content-disposition"] == f'attachment; filename="{CLIP_NAME}"'
    assert response.headers["content-type"] == "video/mp4"


def test_the_copy_goes_30_minutes_after_the_first_complete_download(harness):
    volunteer = enroll_new(harness, WorkerRole.WORKER, "volunteer", "100.70.0.2")
    job_id = ready_job(harness, volunteer)
    render_to_outbox(harness, volunteer, job_id)
    url = ask_for_clip(harness, job_id)["download_url"]
    harness.clock.advance(5 * MINUTE)

    download(harness, url, browser_cookie(harness))

    # The page now shows when it was downloaded and when the copy goes.
    status = clip_status(harness, job_id)
    assert status["downloaded_at"] == "2026-10-25T12:05:00Z"
    assert status["expires_at"] == "2026-10-25T12:35:00Z"
    # A second download (say, by another reviewer) does not move that time.
    harness.clock.advance(10 * MINUTE)
    download(harness, url, browser_cookie(harness))
    assert clip_status(harness, job_id)["expires_at"] == "2026-10-25T12:35:00Z"

    # 29 minutes after the first download: still there, to retry a broken one.
    harness.clock.advance(19 * MINUTE)
    assert sweep_requested_clips(harness.context) == []
    harness.clock.advance(1 * MINUTE)
    assert sweep_requested_clips(harness.context) == [job_id]

    assert not harness.context.layout.requested_clip_path(job_id).exists()
    assert clip_status(harness, job_id)["state"] == "not_requested"
    # The outbox copy, which the studio still needs, is untouched.
    assert harness.context.layout.outbox_path(job_id).exists()


def test_a_copy_nobody_downloads_goes_24_hours_after_the_request(harness):
    volunteer = enroll_new(harness, WorkerRole.WORKER, "volunteer", "100.70.0.2")
    job_id = ready_job(harness, volunteer)
    render_to_outbox(harness, volunteer, job_id)
    ask_for_clip(harness, job_id)

    harness.clock.advance(23 * HOUR)
    assert sweep_requested_clips(harness.context) == []
    harness.clock.advance(1 * HOUR)
    assert sweep_requested_clips(harness.context) == [job_id]


def test_both_times_are_settings(harness):
    settings = harness.client.get("/admin/settings", auth=harness.admin_auth).json()["settings"]
    assert settings["clip_download_keep_minutes"] == 30
    assert settings["clip_download_unused_hours"] == 24
    assert settings["clip_previews_enabled"] is True

    harness.client.patch(
        "/admin/settings", json={"clip_download_unused_hours": 2}, auth=harness.admin_auth
    ).raise_for_status()
    volunteer = enroll_new(harness, WorkerRole.WORKER, "volunteer", "100.70.0.2")
    job_id = ready_job(harness, volunteer)
    render_to_outbox(harness, volunteer, job_id)
    ask_for_clip(harness, job_id)
    harness.clock.advance(2 * HOUR)
    assert sweep_requested_clips(harness.context) == [job_id]


def test_a_download_that_breaks_off_does_not_count(harness, tmp_path):
    # The bytes a download sends, as the web layer asks for them: it sends
    # each piece before asking for the next.
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"x" * (3 * 1024 * 1024))  # three pieces of 1 MiB
    completions = []

    pieces = requested_clip_chunks(clip, 0, clip.stat().st_size - 1, lambda: completions.append(1))
    next(pieces)
    next(pieces)
    # The browser goes away: the web layer closes the generator mid-file.
    pieces.close()
    assert completions == []

    # A range that stops before the end is not a complete download either...
    list(requested_clip_chunks(clip, 0, 999, lambda: completions.append(1)))
    assert completions == []
    # ...but one that reaches the last byte is (a resumed download finishing).
    list(
        requested_clip_chunks(
            clip, 2_000_000, clip.stat().st_size - 1, lambda: completions.append(1)
        )
    )
    assert completions == [1]


def test_a_range_request_is_answered_with_that_range(harness):
    volunteer = enroll_new(harness, WorkerRole.WORKER, "volunteer", "100.70.0.2")
    job_id = ready_job(harness, volunteer)
    render_to_outbox(harness, volunteer, job_id)
    url = ask_for_clip(harness, job_id)["download_url"]
    cookie = browser_cookie(harness)

    response = download(harness, url, cookie, Range="bytes=0-99")

    assert response.status_code == 206
    assert response.content == SAMPLE_CLIP[:100]
    assert response.headers["content-range"] == f"bytes 0-99/{len(SAMPLE_CLIP)}"
    # Only part of the file: the copy's clock has not started.
    assert clip_status(harness, job_id)["downloaded_at"] is None

    # Resuming from byte 100 to the end completes the download.
    download(harness, url, cookie, Range="bytes=100-")
    assert clip_status(harness, job_id)["downloaded_at"] is not None

    # A range past the end of the file is refused with the file's size.
    response = download(harness, url, cookie, Range=f"bytes={len(SAMPLE_CLIP)}-")
    assert response.status_code == 416
    assert response.headers["content-range"] == f"bytes */{len(SAMPLE_CLIP)}"


def test_a_tight_disk_deletes_downloaded_copies_first(harness):
    volunteer = enroll_new(harness, WorkerRole.WORKER, "volunteer", "100.70.0.2")
    waiting = ready_job(harness, volunteer, car_number=7)
    render_to_outbox(harness, volunteer, waiting)
    ask_for_clip(harness, waiting)
    harness.clock.advance(MINUTE)
    downloaded = ready_job(harness, volunteer, car_number=8)
    render_to_outbox(harness, volunteer, downloaded, clip=SAMPLE_CLIP + b"-other")
    url = ask_for_clip(harness, downloaded)["download_url"]
    download(harness, url, browser_cookie(harness))

    # One byte below the eviction threshold: deleting any one copy is enough.
    threshold = harness.context.admin_settings.proxy_evict_min_free_bytes
    harness.context.layout._free_bytes_reader = lambda path: threshold - 1

    # The downloaded one goes, although it was asked for later.
    assert sweep_requested_clips(harness.context) == [downloaded]
    assert clip_status(harness, waiting)["state"] == "ready"


def test_asking_again_after_a_reprocess_gives_the_new_version(harness):
    volunteer = enroll_new(harness, WorkerRole.WORKER, "volunteer", "100.70.0.2")
    job_id = ready_job(harness, volunteer)
    render_to_outbox(harness, volunteer, job_id)
    ask_for_clip(harness, job_id)
    # The studio takes version 1 from the outbox and ACKs it.
    # (Not needed here: a reprocess may come at any time.)
    reprocess(harness, job_id, cut_in_s=2.0)
    second_clip = SAMPLE_CLIP + b"-v2"
    render_to_outbox(harness, volunteer, job_id, clip=second_clip)

    answer = ask_for_clip(harness, job_id)

    assert (answer["state"], answer["version"]) == ("ready", 2)
    assert answer["file_name"] == "enduro-042-p1-v2.mp4"
    assert harness.context.layout.requested_clip_path(job_id).read_bytes() == second_clip


def test_a_job_without_a_clip_cannot_be_asked_for(harness):
    volunteer = enroll_new(harness, WorkerRole.WORKER, "volunteer", "100.70.0.2")
    job_id = ready_job(harness, volunteer)
    response = harness.client.post(f"/jobs/{job_id}/clip-download", auth=harness.reviewer_auth)
    assert response.status_code == 409
    assert clip_status(harness, job_id)["state"] == "not_requested"


def test_without_a_session_nothing_is_given(harness):
    volunteer = enroll_new(harness, WorkerRole.WORKER, "volunteer", "100.70.0.2")
    job_id = ready_job(harness, volunteer)
    render_to_outbox(harness, volunteer, job_id)
    url = ask_for_clip(harness, job_id)["download_url"]

    # No session: neither the request nor its status...
    assert harness.client.post(f"/jobs/{job_id}/clip-download").status_code == 401
    assert harness.client.get(f"/jobs/{job_id}/clip-download").status_code == 401
    # ...nor the file, with no cookie or a made-up one.
    for cookie in ("", "bvp_session=forged"):
        response = download(harness, url, cookie)
        assert response.status_code == 401, cookie
        assert response.content != SAMPLE_CLIP
    # And an attempt never counts as a download.
    assert clip_status(harness, job_id)["downloaded_at"] is None
