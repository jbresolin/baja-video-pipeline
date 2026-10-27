"""process: render the final clip and deliver it (docs/api.md §5, design §7).

The orchestrator decides the delivery route and puts it in the offer as
`delivery` (docs/api.md §5, "Delivery decision"):
  outbox  chunked upload to the VPS outbox
  direct  push to the studio's receiver at `delivery.studio_url`; the
          studio's ACK ends our lease. If the push fails, or no ACK ended
          the lease within the wait (60 s in real life, 0.3 s in these
          tests), the worker uploads to the outbox instead.
Failure reports and their scope are in test_process_failures.py.
"""

import json
from dataclasses import replace

import worker.tasks.clip_delivery as clip_delivery
from shared.models import DeliveryPlan, DeliveryRoute, ProcessTask
from tests.worker.conftest import OUTBOX_DELIVERY, STUDIO_URL, direct_delivery
from tests.worker.fake_orchestrator import BASE_URL


def hold_raw(harness, raw_bytes):
    job = harness.orchestrator.add_job(
        raw_bytes, prova="Enduro", car_number=42, team_name="Equipe Teste", passagem=3
    )
    harness.storage.raw_path(job.job_id, ".mp4").write_bytes(raw_bytes)
    return job


def process_task(job, **changes) -> ProcessTask:
    """The offer for `job`; delivery through the outbox unless a test says otherwise."""
    fields = dict(
        job_id=job.job_id,
        raw_sha256=job.sha256,
        has_audio=True,
        low_res=False,
        cut_in_s=2.0,
        cut_out_s=9.5,
        prova="Enduro",
        car_number=42,
        team_name="Equipe Teste",
        passagem=3,
        show_pass=True,
        clip_relative_path="enduro/42/3.mp4",
        outbox_upload_url=f"{BASE_URL}/jobs/{job.job_id}/clip",
        delivery=OUTBOX_DELIVERY,
    )
    fields.update(changes)
    return ProcessTask(**fields)


def test_render_command_uses_cuts_overlay_and_loudnorm(harness, raw_bytes):
    job = hold_raw(harness, raw_bytes)

    harness.claim_and_run(process_task(job))

    command = harness.media.command_for("final render")
    # Trim: start at the in point, last (out - in) seconds.
    assert command[command.index("-ss") + 1] == "2.000"
    assert command[command.index("-t") + 1] == "7.500"
    # The overlay PNG drawn by the worker is the second input, placed
    # bottom-left (x=0, y=bottom) by the shared builder.
    assert any(arg.endswith(".overlay.png") for arg in command)
    assert "overlay=x=0:y=main_h-overlay_h" in " ".join(command)
    # Loudness normalisation is in the audio chain.
    assert "loudnorm" in " ".join(command)


def test_outbox_delivery_uploads_to_the_outbox_and_never_contacts_the_studio(harness, raw_bytes):
    # Setup: a studio receiver is running, but the offer says "outbox"
    # (for example, the studio's last heartbeat is too old). The worker
    # must follow the offer, not look for a studio itself.
    job = hold_raw(harness, raw_bytes)
    harness.add_studio()

    harness.claim_and_run(process_task(job, delivery=OUTBOX_DELIVERY))

    # The clip landed in the VPS outbox through the chunked upload.
    stored = harness.orchestrator.stored_upload("outbox", job.job_id)
    assert stored.read_bytes().startswith(b"fake final render")
    # Not a single request went to the studio.
    assert harness.network.headers_sent_to("studio.test") == []
    # Once delivered, the local copy is no longer needed.
    assert not harness.storage.clip_path(job.job_id).exists()
    assert harness.orchestrator.failures == []


def test_direct_push_to_the_studio_and_ack_ends_the_lease(harness, raw_bytes, monkeypatch):
    # Setup: the offer says "direct" with the studio's receive URL; the
    # studio's receiver is the real app.
    job = hold_raw(harness, raw_bytes)
    library, acknowledger = harness.add_studio()

    # In real life, while the worker waits after the push, two things
    # happen in the background: the studio ACKs to the orchestrator, and
    # our heartbeat then hears that the lease is "lost" (= finished). To
    # keep the test free of timing, we run both right when the wait starts.
    real_wait = clip_delivery._studio_ack_ended_lease

    def studio_acts_then_wait(env, lease, cancel):
        acknowledger.handle_all_pending()  # studio: latest.m3u + POST /delivered
        harness.heartbeater.beat_once()  # our heartbeat: lease lost
        return real_wait(env, lease, cancel)

    monkeypatch.setattr(clip_delivery, "_studio_ack_ended_lease", studio_acts_then_wait)

    harness.claim_and_run(process_task(job, delivery=direct_delivery(job.job_id)))

    # The clip is on the box at <prova>/<car>/<passagem>.mp4...
    assert (library.root / "enduro/42/3.mp4").read_bytes().startswith(b"fake final render")
    # ...the studio ACKed it to the orchestrator...
    [(acked_job_id, delivered)] = harness.orchestrator.delivered
    assert acked_job_id == job.job_id and delivered.relative_path == "enduro/42/3.mp4"
    # ...and the worker did NOT also upload it to the outbox.
    assert not harness.orchestrator.stored_upload("outbox", job.job_id).exists()
    assert not harness.storage.clip_path(job.job_id).exists()


def test_direct_push_never_carries_the_bearer_token(harness, raw_bytes):
    # The studio (:8720) is not one of the two origins allowed to see our
    # token (design §17). We look at the headers of every push request.
    job = hold_raw(harness, raw_bytes)
    harness.add_studio()

    harness.claim_and_run(process_task(job, delivery=direct_delivery(job.job_id)))

    studio_requests = harness.network.headers_sent_to("studio.test")
    assert studio_requests, "the clip should have been pushed to the studio"
    assert all("authorization" not in headers for headers in studio_requests)


def test_studio_url_from_the_offer_is_used_exactly(harness, raw_bytes):
    # The worker pushes to the URL the orchestrator gave, path included;
    # it does not build its own from a host and port.
    job = hold_raw(harness, raw_bytes)
    harness.add_studio()

    harness.claim_and_run(process_task(job, delivery=direct_delivery(job.job_id)))

    pushed_urls = [url for url in harness.network.requested_urls if "studio.test" in url]
    assert pushed_urls
    assert all(url == f"{STUDIO_URL}/receive/{job.job_id}" for url in pushed_urls)


def test_push_without_ack_falls_back_to_the_outbox_after_the_wait(harness, raw_bytes):
    # Setup: the studio receives the push, but its ACK never reaches the
    # orchestrator (we never run its acknowledger), so our lease keeps
    # being renewed. After the wait the worker must use the outbox too.
    job = hold_raw(harness, raw_bytes)
    library, _ = harness.add_studio()

    harness.claim_and_run(process_task(job, delivery=direct_delivery(job.job_id)))

    assert (library.root / "enduro/42/3.mp4").exists()
    assert harness.orchestrator.stored_upload("outbox", job.job_id).exists()


def test_studio_offline_falls_back_to_the_outbox(harness, raw_bytes):
    # Setup: the offer says "direct", but nothing answers at the studio's
    # address (it went offline after its last heartbeat).
    job = hold_raw(harness, raw_bytes)

    harness.claim_and_run(process_task(job, delivery=direct_delivery(job.job_id)))

    assert harness.orchestrator.stored_upload("outbox", job.job_id).exists()
    assert harness.orchestrator.failures == []


def test_direct_without_a_studio_url_uses_the_outbox(harness, raw_bytes):
    # A malformed offer ("direct" but no URL) should never happen; the
    # outbox always works, so the worker uses it rather than failing.
    job = hold_raw(harness, raw_bytes)
    plan = DeliveryPlan(mode=DeliveryRoute.DIRECT, studio_url=None)

    harness.claim_and_run(process_task(job, delivery=plan))

    assert harness.orchestrator.stored_upload("outbox", job.job_id).exists()


def test_lease_lost_mid_render_discards_the_clip_and_uploads_nothing(harness, raw_bytes):
    # Setup: during the render the reviewer changes the cut ("reprocess"),
    # which revokes our lease; our next heartbeat learns it is lost.
    job = hold_raw(harness, raw_bytes)

    def reviewer_reprocesses(what):
        harness.orchestrator.revoke_lease(harness.leases.ids()[0])
        harness.heartbeater.beat_once()

    harness.media.during_render = reviewer_reprocesses

    harness.claim_and_run(process_task(job))

    # No upload, no failure report, no clip left behind.
    assert not harness.orchestrator.stored_upload("outbox", job.job_id).exists()
    assert harness.orchestrator.failures == []
    assert not harness.storage.clip_path(job.job_id).exists()
    assert list(harness.storage.tmp_dir.iterdir()) == []


def test_outbox_upload_resumes_after_a_crash_without_rendering_again(harness, raw_bytes):
    # Setup, part 1: small 4 KB chunks, and the worker "crashes" (we use a
    # shutdown, which stops the task the same way) right after the outbox
    # accepted its second chunk.
    job = hold_raw(harness, raw_bytes)
    task = process_task(job)
    harness.env.settings = replace(harness.env.settings, upload_chunk_bytes=4096)

    def crash_after_two_chunks(kind, received_bytes):
        if kind == "outbox" and received_bytes == 8192:
            harness.shutdown.set()

    harness.orchestrator.after_chunk_accepted = crash_after_two_chunks
    harness.claim_and_run(task)

    # The rendered clip and its sidecar survived the "crash".
    renders_in_first_run = len(harness.media.commands)
    assert harness.storage.clip_path(job.job_id).exists()
    sidecar = json.loads(harness.storage.clip_sidecar_path(job.job_id).read_text())
    assert sidecar["cut_in_s"] == 2.0

    # Part 2: the worker starts again; the orchestrator offers the job again.
    harness.shutdown.clear()
    harness.orchestrator.after_chunk_accepted = None
    log_length_before = len(harness.orchestrator.upload_log)
    harness.claim_and_run(task)

    # No second render: the finished clip was reused...
    assert len(harness.media.commands) == renders_in_first_run
    # ...and the upload resumed. The client starts at 0, the receiver
    # answers 409 "I already have 8192 bytes", and the client continues
    # from there instead of sending everything again.
    second_run = harness.orchestrator.upload_log[log_length_before:]
    assert second_run[0] == ("outbox", 409, 0)
    assert second_run[1] == ("outbox", 200, 8192)
    stored = harness.orchestrator.stored_upload("outbox", job.job_id)
    assert stored.read_bytes().startswith(b"fake final render")


def test_changed_cut_is_rendered_again_even_if_an_old_clip_exists(harness, raw_bytes):
    job = hold_raw(harness, raw_bytes)
    # An old clip rendered with other cuts is lying in clips/.
    harness.storage.clip_path(job.job_id).write_bytes(b"old clip")
    old_inputs = {"cut_in_s": 0.0}
    harness.storage.clip_sidecar_path(job.job_id).write_text(json.dumps(old_inputs))

    harness.claim_and_run(process_task(job))

    # The render ran, and the outbox got the new clip, not the old bytes.
    assert harness.media.command_for("final render")
    stored = harness.orchestrator.stored_upload("outbox", job.job_id)
    assert stored.read_bytes().startswith(b"fake final render")
