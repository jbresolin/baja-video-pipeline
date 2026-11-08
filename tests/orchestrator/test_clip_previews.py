""" "Ver clipe": the preview of every rendered clip (LIVESTACK-52).

What a reviewer gets on the job page and the clips page, and how the VPS
keeps it:
  * a render task asks for a preview while the Settings switch "Gerar
    prévia dos clipes" is on, and the worker that rendered the clip
    uploads it after the delivery (no lease by then);
  * the job then has a preview to play; before that the page says it is
    being made, and later why there is none (switch off, an older worker,
    evicted, never arrived);
  * a reprocessed clip replaces its preview: the job shows only the newest;
  * previews are evicted like proxies, only when the disk is tight;
  * only the worker that rendered the clip may send its preview.

Each test builds a fresh orchestrator (the `harness` fixture) whose clock
starts at 2026-10-25 12:00 UTC and moves only when a test advances it.
"""

import os
import time

from orchestrator.domain.clip_previews import evict_previews_once
from orchestrator.testing.scenarios import SAMPLE_CLIP, ask_next
from shared.models import TaskKind, WorkerRole
from tests.orchestrator.clip_review_helpers import (
    HOUR,
    MINUTE,
    SAMPLE_PREVIEW,
    beat,
    enroll_new,
    master_volunteer_studio,
    ready_job,
    render_to_outbox,
    render_to_studio,
    reprocess,
    send_preview,
    sha256_of,
)

CLIP_SHA256 = sha256_of(SAMPLE_CLIP)


def set_previews(harness, enabled: bool) -> None:
    """The admin flips "Gerar prévia dos clipes" on the Settings page."""
    harness.client.patch(
        "/admin/settings", json={"clip_previews_enabled": enabled}, auth=harness.admin_auth
    ).raise_for_status()


def test_a_render_asks_for_a_preview_and_the_renderer_uploads_it(harness):
    # A volunteer on 0.7.0 holds a reviewed job and is offered the render.
    volunteer = enroll_new(harness, WorkerRole.WORKER, "volunteer", "100.70.0.2")
    job_id = ready_job(harness, volunteer)
    offer = ask_next(harness, volunteer, [TaskKind.PROCESS])
    # The task says where to send the preview (the switch is on by default).
    assert offer["preview_upload_url"].endswith(f"/jobs/{job_id}/clip-preview")

    render_to_outbox(harness, volunteer, job_id)
    # Rendered and delivered, the preview not here yet: "being made".
    assert harness.job(job_id)["clip_preview"]["state"] == "making"

    response = send_preview(harness, volunteer, job_id, CLIP_SHA256)

    assert response.status_code == 200 and response.json()["complete"] is True
    preview = harness.job(job_id)["clip_preview"]
    # The page gets a URL under /previews/ (Caddy serves it to signed-in
    # users), one name per clip version, and the version it shows.
    assert preview == {
        "state": "ready",
        "url": f"/previews/{job_id}.{CLIP_SHA256}.mp4",
        "version": 1,
        "size_bytes": len(SAMPLE_PREVIEW),
    }
    on_disk = harness.context.layout.preview_path(job_id, CLIP_SHA256)
    assert on_disk.read_bytes() == SAMPLE_PREVIEW


def test_a_clip_pushed_to_the_studio_gets_its_preview_too(harness):
    # The other delivery route: the render goes straight to the studio,
    # whose ACK records the clip version. The renderer sends the preview after.
    _, volunteer, studio = master_volunteer_studio(harness)
    job_id = ready_job(harness, volunteer)
    render_to_studio(harness, volunteer, studio, job_id)

    assert send_preview(harness, volunteer, job_id, CLIP_SHA256).json()["complete"] is True
    assert harness.job(job_id)["clip_preview"]["state"] == "ready"


def test_the_clips_page_rows_carry_the_preview(harness):
    # The clips page reads the manifest; each delivered clip has its preview.
    _, volunteer, studio = master_volunteer_studio(harness)
    job_id = ready_job(harness, volunteer)
    render_to_studio(harness, volunteer, studio, job_id)
    send_preview(harness, volunteer, job_id, CLIP_SHA256)

    manifest = harness.client.get("/manifest", auth=harness.reviewer_auth).json()

    assert manifest["clips"][0]["preview"]["url"] == f"/previews/{job_id}.{CLIP_SHA256}.mp4"


def test_with_the_switch_off_no_preview_is_asked_for_and_the_page_says_why(harness):
    set_previews(harness, False)
    volunteer = enroll_new(harness, WorkerRole.WORKER, "volunteer", "100.70.0.2")
    job_id = ready_job(harness, volunteer)

    task = render_to_outbox(harness, volunteer, job_id)

    # The task had nowhere to send a preview, so the worker makes none...
    assert task["preview_upload_url"] is None
    # ...and the page explains it instead of waiting for one.
    assert harness.job(job_id)["clip_preview"]["state"] == "switched_off"


def test_a_worker_before_0_7_0_sends_no_preview_and_the_page_says_so(harness):
    # This volunteer declares none of the new abilities (an older installer).
    volunteer = harness.enroll(WorkerRole.WORKER, "old-pc", tailnet_address="100.70.0.9")
    job_id = ready_job(harness, volunteer)

    render_to_outbox(harness, volunteer, job_id)

    # The render was not held back for it (a preview is never required).
    assert harness.job(job_id)["state"] == "uploaded"
    assert harness.job(job_id)["clip_preview"]["state"] == "older_worker"


def test_a_preview_that_never_comes_is_called_missing_after_15_minutes(harness):
    volunteer = enroll_new(harness, WorkerRole.WORKER, "volunteer", "100.70.0.2")
    job_id = ready_job(harness, volunteer)
    render_to_outbox(harness, volunteer, job_id)

    harness.clock.advance(14 * MINUTE)
    assert harness.job(job_id)["clip_preview"]["state"] == "making"
    harness.clock.advance(2 * MINUTE)
    # The worker failed or quit after delivering: the page stops waiting.
    assert harness.job(job_id)["clip_preview"]["state"] == "missing"


def test_a_job_without_a_clip_has_no_preview_block(harness):
    volunteer = enroll_new(harness, WorkerRole.WORKER, "volunteer", "100.70.0.2")
    job_id = ready_job(harness, volunteer)
    assert harness.job(job_id)["clip_preview"] is None


def test_only_the_worker_that_rendered_the_clip_may_send_its_preview(harness):
    master, volunteer, _ = master_volunteer_studio(harness)
    job_id = ready_job(harness, volunteer)
    render_to_outbox(harness, volunteer, job_id)

    # The master did not render it: refused, and nothing is stored.
    response = send_preview(harness, master, job_id, CLIP_SHA256)

    assert response.status_code == 403
    assert not harness.context.layout.preview_path(job_id, CLIP_SHA256).exists()


def test_a_preview_names_the_clip_it_was_made_from(harness):
    volunteer = enroll_new(harness, WorkerRole.WORKER, "volunteer", "100.70.0.2")
    job_id = ready_job(harness, volunteer)
    render_to_outbox(harness, volunteer, job_id)

    # A SHA-256 that is no clip of this job is refused (409)...
    assert send_preview(harness, volunteer, job_id, "f" * 64).status_code == 409
    # ...and one that is not a SHA-256 at all is a bad request (400).
    assert send_preview(harness, volunteer, job_id, "not-a-hash").status_code == 400


def test_a_reprocessed_clip_replaces_its_preview(harness):
    # Version 1 is rendered and gets its preview.
    volunteer = enroll_new(harness, WorkerRole.WORKER, "volunteer", "100.70.0.2")
    job_id = ready_job(harness, volunteer)
    render_to_outbox(harness, volunteer, job_id)
    send_preview(harness, volunteer, job_id, CLIP_SHA256)
    first_file = harness.context.layout.preview_path(job_id, CLIP_SHA256)

    # The reviewer moves the cut and asks for a new render: version 2.
    reprocess(harness, job_id, cut_in_s=2.0)
    second_clip = SAMPLE_CLIP + b"-v2"
    render_to_outbox(harness, volunteer, job_id, clip=second_clip)

    # Until version 2's preview arrives, the page does not show version 1's:
    # it would show the old cut as if it were the clip on air.
    preview = harness.job(job_id)["clip_preview"]
    assert (preview["state"], preview["version"]) == ("making", 2)
    # A late preview of version 1 is refused now (a newer clip replaced it).
    assert send_preview(harness, volunteer, job_id, CLIP_SHA256).status_code == 409

    send_preview(harness, volunteer, job_id, sha256_of(second_clip), data=b"preview-v2" * 50)

    preview = harness.job(job_id)["clip_preview"]
    assert (preview["state"], preview["version"]) == ("ready", 2)
    # Older versions keep no preview: version 1's file is gone.
    assert not first_file.exists()


def test_the_cleanup_deletes_previews_of_replaced_versions(harness):
    # A stray preview file of a version that is no longer the newest (say
    # the sweeper ran while version 2's upload was finishing elsewhere).
    volunteer = enroll_new(harness, WorkerRole.WORKER, "volunteer", "100.70.0.2")
    job_id = ready_job(harness, volunteer)
    render_to_outbox(harness, volunteer, job_id)
    send_preview(harness, volunteer, job_id, CLIP_SHA256)
    stray = harness.context.layout.preview_path(job_id, "e" * 64)
    stray.write_bytes(b"old")
    # Files younger than 10 minutes are left alone (an upload may have just
    # finished), so make this one look older.
    an_hour_ago = time.time() - HOUR
    os.utime(stray, (an_hour_ago, an_hour_ago))

    evict_previews_once(harness.context)

    assert not stray.exists()
    # The job's own preview stays: the disk is not tight.
    assert harness.context.layout.preview_path(job_id, CLIP_SHA256).exists()


def test_previews_are_evicted_only_when_the_disk_is_tight(harness):
    volunteer = enroll_new(harness, WorkerRole.WORKER, "volunteer", "100.70.0.2")
    job_id = ready_job(harness, volunteer)
    render_to_outbox(harness, volunteer, job_id)
    send_preview(harness, volunteer, job_id, CLIP_SHA256)

    # Plenty of room (the test's disk): nothing happens.
    assert evict_previews_once(harness.context) == []
    # Below the proxy eviction threshold (Settings, 10 GB by default).
    harness.context.layout._free_bytes_reader = lambda path: 1_000_000

    assert evict_previews_once(harness.context) == [job_id]

    # The page says why there is no preview any more, and offers the download.
    assert harness.job(job_id)["clip_preview"]["state"] == "evicted"


def test_finished_jobs_lose_their_previews_first(harness):
    # Two clips with previews: one job finished (done), one still delivering.
    _, volunteer, studio = master_volunteer_studio(harness)
    beat(harness, volunteer)
    delivering = ready_job(harness, volunteer, car_number=7)
    render_to_outbox(harness, volunteer, delivering)
    send_preview(harness, volunteer, delivering, CLIP_SHA256)
    harness.clock.advance(MINUTE)
    finished = ready_job(harness, volunteer, car_number=8)
    other_clip = SAMPLE_CLIP + b"-other"
    render_to_studio(harness, volunteer, studio, finished, clip=other_clip)
    send_preview(harness, volunteer, finished, sha256_of(other_clip))
    assert harness.job(finished)["state"] == "done"

    # The disk is tight, and freeing one file is enough.
    layout = harness.context.layout
    tight = iter([1_000_000, 50_000_000_000])
    layout._free_bytes_reader = lambda path: next(tight, 50_000_000_000)

    # The finished job's preview goes, although it is the newer one.
    assert evict_previews_once(harness.context) == [finished]
