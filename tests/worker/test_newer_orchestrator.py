"""A worker and a studio keep working when the orchestrator is newer than they are.

Design §14: a client must never break because the server added a field.
Workers are upgraded by volunteers, so for a while some run an older
version than the orchestrator. Everything they read from it (heartbeat
answers, task offers, claims, GET /jobs/{id}, chunk answers) ignores
fields it does not know.

`build_harness(..., newer_server=True)` puts shared.testing.newer_server
between the worker and the fake orchestrator: every JSON answer gains an
unknown field at the top level and inside every nested object (the
lease, the task, its delivery plan, the job's holders...). The tests then
run the real worker code and check that the work still gets done.
"""

from shared.hashing import sha256_of_bytes
from shared.models import DeliverTask, WorkerRole
from tests.worker.conftest import build_harness
from tests.worker.fake_orchestrator import BASE_URL
from tests.worker.test_process import hold_raw, process_task

CLIP = b"finished clip " * 2000


def test_a_worker_heartbeats_claims_renders_and_uploads(tmp_path, raw_bytes):
    harness = build_harness(tmp_path, newer_server=True)
    job = hold_raw(harness, raw_bytes)

    # The heartbeat answer is read (None would mean "could not talk to it").
    assert harness.heartbeater.beat_once() is not None
    # Offer, claim, render, report the render inputs, upload in chunks.
    harness.claim_and_run(process_task(job))

    # The clip reached the outbox and the worker reported no failure.
    assert harness.orchestrator.stored_upload("outbox", job.job_id).exists()
    assert harness.orchestrator.failures == []
    # And the answers really did carry the unknown fields (heartbeat, next,
    # claim, chunk answers...), so the test is not passing by accident.
    assert harness.answers.widened_answers >= 4


def test_a_studio_pulls_a_clip_and_titles_it_from_a_newer_job_record(tmp_path):
    harness = build_harness(tmp_path, role=WorkerRole.STUDIO, newer_server=True)
    job = harness.orchestrator.add_job(b"raw", prova="Suspensão", car_number=7, passagem=1)
    harness.orchestrator.outbox[f"{job.job_id}.mp4"] = CLIP
    task = DeliverTask(
        job_id=job.job_id,
        download_url=f"{BASE_URL}/outbox/{job.job_id}.mp4",
        sha256=sha256_of_bytes(CLIP),
        size_bytes=len(CLIP),
        clip_relative_path="suspensao/7/1.mp4",
    )

    harness.claim_and_run(task)

    # Stored, and the playlist title comes from GET /jobs/{id}, which the
    # studio parsed with the unknown fields in it.
    library = harness.env.studio_library
    assert (library.root / "suspensao/7/1.mp4").read_bytes() == CLIP
    playlist = (library.root / "latest.m3u").read_text()
    assert "#EXTINF:-1,Suspensão - carro 7 - passagem 1\nsuspensao/7/1.mp4" in playlist
    # The ACK went through: the orchestrator knows the clip is on the box.
    [(acked_job, _)] = harness.orchestrator.outbox_acks
    assert acked_job == job.job_id
