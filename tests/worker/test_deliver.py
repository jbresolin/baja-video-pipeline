"""deliver (studio): pull an outbox clip into the box root and ACK (docs/api.md §5)."""

from shared.hashing import sha256_of_bytes
from shared.http_transfer import partial_path_for
from shared.models import DeliverTask, TaskKind, WorkerRole
from tests.worker.conftest import build_harness
from tests.worker.fake_orchestrator import BASE_URL, BEARER_TOKEN

CLIP = b"finished clip " * 2000


def studio_with_outbox_clip(tmp_path):
    """A studio harness and a job whose clip waits in the VPS outbox."""
    harness = build_harness(tmp_path, role=WorkerRole.STUDIO)
    job = harness.orchestrator.add_job(b"raw", prova="Suspensão", car_number=7, passagem=1)
    harness.orchestrator.outbox[f"{job.job_id}.mp4"] = CLIP
    task = DeliverTask(
        job_id=job.job_id,
        download_url=f"{BASE_URL}/outbox/{job.job_id}.mp4",
        sha256=sha256_of_bytes(CLIP),
        size_bytes=len(CLIP),
        clip_relative_path="suspensao/7/1.mp4",
    )
    return harness, job, task


def test_studio_only_asks_for_deliver_tasks(tmp_path):
    harness, job, task = studio_with_outbox_clip(tmp_path)
    harness.dispatcher.run_once()
    # The studio never volunteers as a holder (design §13).
    assert harness.orchestrator.next_requests[0].accept_kinds == [TaskKind.DELIVER]


def test_outbox_clip_is_stored_listed_and_acked(tmp_path):
    harness, job, task = studio_with_outbox_clip(tmp_path)

    harness.claim_and_run(task)

    library = harness.env.studio_library
    # Stored at the contract's path, complete.
    assert (library.root / "suspensao/7/1.mp4").read_bytes() == CLIP
    # The outbox (Caddy) got our bearer token.
    [(_, headers)] = harness.orchestrator.file_requests
    assert headers["authorization"] == f"Bearer {BEARER_TOKEN}"
    # latest.m3u shows the prova with its accents, from GET /jobs/{id}.
    playlist = (library.root / "latest.m3u").read_text()
    assert "#EXTINF:-1,Suspensão - carro 7 - passagem 1\nsuspensao/7/1.mp4" in playlist
    # And the ACK says which file, with which hash, under which lease.
    [(acked_job, ack)] = harness.orchestrator.outbox_acks
    assert acked_job == job.job_id
    assert ack.sha256 == task.sha256 and ack.relative_path == "suspensao/7/1.mp4"


def test_interrupted_pull_resumes(tmp_path):
    # Setup: a previous attempt left the first half in the hidden partial file.
    harness, job, task = studio_with_outbox_clip(tmp_path)
    destination = harness.env.studio_library.root / "suspensao/7/1.mp4"
    destination.parent.mkdir(parents=True)
    partial_path_for(destination).write_bytes(CLIP[:10_000])

    harness.claim_and_run(task)

    [(_, headers)] = harness.orchestrator.file_requests
    assert headers["range"] == "bytes=10000-"
    assert destination.read_bytes() == CLIP


def test_corrupt_outbox_clip_is_not_stored_or_acked(tmp_path):
    harness, job, task = studio_with_outbox_clip(tmp_path)
    harness.orchestrator.outbox[f"{job.job_id}.mp4"] = b"garbage"

    harness.claim_and_run(task)

    # Nothing visible for OBS, no ACK (the lease expires and it is retried).
    assert not (harness.env.studio_library.root / "suspensao/7/1.mp4").exists()
    assert harness.orchestrator.outbox_acks == []
