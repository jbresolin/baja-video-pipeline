"""The preview of a rendered clip ("Ver clipe", LIVESTACK-52), on the worker side.

After a process task has delivered its clip, a 0.7.0 worker makes a 480p
preview of it (the review proxy's settings) and uploads it to the task's
`preview_upload_url`, naming the clip it was made from in the Clip-SHA256
header. These tests run the real process task against the in-memory fake
orchestrator (tests/worker/fake_orchestrator.py) with fake ffmpeg (each
"render" writes a few bytes of text).
"""

import httpx

import worker.tasks.clip_delivery as clip_delivery
from shared.hashing import sha256_of_file
from shared.models import RenderFeature
from tests.worker.conftest import direct_delivery
from tests.worker.fake_orchestrator import BASE_URL
from tests.worker.test_process import hold_raw, process_task
from worker.render_features import DECLARED_RENDER_FEATURES


def with_preview(job, **changes):
    """The usual render offer, with somewhere to send the preview."""
    return process_task(
        job, preview_upload_url=f"{BASE_URL}/jobs/{job.job_id}/clip-preview", **changes
    )


def test_after_the_delivery_a_480p_preview_of_the_clip_is_uploaded(harness, raw_bytes):
    job = hold_raw(harness, raw_bytes)

    harness.claim_and_run(with_preview(job))

    # The preview was made from the finished clip (the kept file), not the raw...
    command = harness.media.command_for("clip preview")
    kept_clip = harness.storage.clips.find(job.job_id)
    assert command[command.index("-i") + 1] == str(kept_clip)
    # ...at the review proxy's size and bitrate.
    assert "scale=-2:480" in command[command.index("-vf") + 1]
    assert command[command.index("-b:v") + 1] == "1500k"
    # It reached the VPS, saying which clip it shows.
    preview = harness.orchestrator.stored_upload("previews", job.job_id)
    assert preview.read_bytes().startswith(b"fake clip preview")
    assert set(harness.orchestrator.preview_clip_sha256_headers) == {sha256_of_file(kept_clip)}
    # The clip went to the outbox first: the clip on air never waits for a preview.
    kinds = [kind for kind, _, _ in harness.orchestrator.upload_log]
    assert kinds.index("outbox") < kinds.index("previews")
    assert harness.orchestrator.failures == []


def test_without_a_preview_url_no_preview_is_made(harness, raw_bytes):
    # "Gerar prévia dos clipes" is off (or the orchestrator predates previews).
    job = hold_raw(harness, raw_bytes)

    harness.claim_and_run(process_task(job))

    assert "clip preview" not in [what for what, _ in harness.media.commands]
    assert "previews" not in [kind for kind, _, _ in harness.orchestrator.upload_log]


def test_a_failing_preview_never_fails_the_render(harness, raw_bytes):
    # ffmpeg fails on the preview: the clip is delivered and kept all the same,
    # and nothing is reported (the job page will say the preview did not come).
    job = hold_raw(harness, raw_bytes)
    harness.media.failing_render = "clip preview"

    harness.claim_and_run(with_preview(job))

    assert harness.orchestrator.failures == []
    assert harness.orchestrator.stored_upload("outbox", job.job_id).exists()
    assert harness.storage.clips.find(job.job_id) is not None
    # The temporary preview file is gone either way.
    assert not harness.storage.tmp_path(f"{job.job_id}.preview.mp4").exists()


def test_a_refused_preview_is_dropped_quietly(harness, raw_bytes):
    # The VPS refuses it (say a reprocess replaced the clip meanwhile: 409).
    job = hold_raw(harness, raw_bytes)
    refusing = httpx.MockTransport(
        lambda request: httpx.Response(409, json={"error": {"code": "conflict", "message": ""}})
    )
    harness.network.override_path(BASE_URL, f"/jobs/{job.job_id}/clip-preview", refusing)

    harness.claim_and_run(with_preview(job))

    assert harness.orchestrator.failures == []
    assert not harness.storage.tmp_path(f"{job.job_id}.preview.mp4").exists()


def test_this_version_declares_both_new_abilities():
    # The orchestrator offers "Baixar clipe" uploads only to workers that
    # declare upload_clip, and tells the page an older worker made no
    # preview when clip_preview is missing.
    assert RenderFeature.CLIP_PREVIEW.value in DECLARED_RENDER_FEATURES
    assert RenderFeature.UPLOAD_CLIP.value in DECLARED_RENDER_FEATURES


def test_a_clip_pushed_to_the_studio_still_gets_its_preview(harness, raw_bytes, monkeypatch):
    # Setup: the usual event route. The worker pushes the clip to the studio,
    # the studio ACKs, and our heartbeat then hears the lease is "lost",
    # which here means "finished". The preview is made after that, so it
    # must not take the lost lease as a reason to stop.
    job = hold_raw(harness, raw_bytes)
    _, acknowledger = harness.add_studio()
    real_wait = clip_delivery._studio_ack_ended_lease

    def studio_acts_then_wait(env, lease, cancel):
        acknowledger.handle_all_pending()  # studio: POST /delivered
        harness.heartbeater.beat_once()  # our heartbeat: lease lost (= done)
        return real_wait(env, lease, cancel)

    monkeypatch.setattr(clip_delivery, "_studio_ack_ended_lease", studio_acts_then_wait)

    harness.claim_and_run(with_preview(job, delivery=direct_delivery(job.job_id)))

    # Delivered straight to the studio (not the outbox), and the preview went up.
    assert harness.orchestrator.delivered
    assert not harness.orchestrator.stored_upload("outbox", job.job_id).exists()
    assert harness.orchestrator.stored_upload("previews", job.job_id).exists()
