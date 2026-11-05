"""Worker side of the clip archive (LIVESTACK-38 item 7; design §11 as amended).

Processed clips are the archive, so:
* a worker keeps every clip it delivered (clips/<job>.<sha256>.mp4) and
  serves it on :8710 (GET /clip/{job_id});
* it evicts a kept clip only when the orchestrator says the master has
  archived that exact clip, and only when near its storage cap, oldest
  first (worker.clip_store);
* the master runs archive_clip: its own copy is moved into the archive
  tree, others are downloaded from a peer or the outbox and hash-checked.
"""

import hashlib
import os

import httpx

from shared.models import ArchiveClipTask, ProcessTask, WorkerRole
from tests.worker.conftest import GB, OUTBOX_DELIVERY, PEER_URL, build_harness
from tests.worker.fake_orchestrator import BASE_URL
from worker.servers import build_peer_file_server_app
from worker.storage import Storage

JOB_A = "a" * 32
JOB_B = "b" * 32
CLIP = b"a finished clip " * 2000
CLIP_SHA = hashlib.sha256(CLIP).hexdigest()


def keep_clip(storage, job_id, content, age_seconds=0):
    """Put a delivered clip in clips/ the way process does, optionally aged."""
    rendered = storage.clip_path(job_id)
    rendered.write_bytes(content)
    sha256 = hashlib.sha256(content).hexdigest()
    kept = storage.clips.keep(rendered, job_id, sha256)
    if age_seconds:
        old = kept.stat().st_mtime - age_seconds
        os.utime(kept, (old, old))
    return sha256


# --- Keeping, listing, serving ---


def test_a_processed_clip_is_kept_after_delivery(harness, raw_bytes):
    # Setup: the worker holds a raw and processes it to the outbox.
    job = harness.orchestrator.add_job(raw_bytes)
    harness.storage.raw_path(job.job_id, ".mp4").write_bytes(raw_bytes)
    task = ProcessTask(
        job_id=job.job_id, raw_sha256=job.sha256, has_audio=True, low_res=False,
        cut_in_s=1, cut_out_s=5, prova="Enduro", car_number=42, team_name=None,
        passagem=3, show_pass=True, clip_relative_path="enduro/42/3.mp4",
        outbox_upload_url=f"{BASE_URL}/jobs/{job.job_id}/clip", delivery=OUTBOX_DELIVERY,
    )  # fmt: skip

    harness.claim_and_run(task)

    # The clip went to the outbox AND stays on the worker, named by its hash.
    uploaded = harness.orchestrator.stored_upload("outbox", job.job_id).read_bytes()
    [held] = harness.storage.clips.held_clips()
    assert held.job_id == job.job_id
    assert held.sha256 == hashlib.sha256(uploaded).hexdigest()
    # The next heartbeat tells the orchestrator about it.
    assert harness.heartbeater.build_request().held_clips == [held]


def test_kept_clips_are_served_to_the_master_on_8710(harness):
    sha256 = keep_clip(harness.storage, JOB_A, CLIP)
    harness.network.add_app(
        PEER_URL, build_peer_file_server_app(harness.storage, "worker-1", "worker")
    )
    client = httpx.Client(transport=harness.network)
    # The exact version the archive task asks for...
    exact = client.get(f"{PEER_URL}/clip/{JOB_A}?sha256={sha256}")
    assert exact.status_code == 200 and exact.content == CLIP
    # ...a version we do not keep, and an attempt to escape the folder, are 404s.
    assert client.get(f"{PEER_URL}/clip/{JOB_A}?sha256={'0' * 64}").status_code == 404
    assert client.get(f"{PEER_URL}/clip/{JOB_A}?sha256=../../worker.json").status_code == 404


# --- Eviction ---


def near_cap_storage(tmp_path, kept_bytes):
    """A worker whose cap leaves under 4 GB free once `kept_bytes` are on disk."""
    return Storage(tmp_path / "w", cap_bytes=kept_bytes + 3 * GB, disk_free=lambda _: 500 * GB)


def test_eviction_refuses_clips_the_master_has_not_archived(tmp_path):
    storage = near_cap_storage(tmp_path, len(CLIP))
    keep_clip(storage, JOB_A, CLIP)
    # Near the cap, but the master has archived nothing: nothing is deleted.
    evicted = storage.clips.evict_archived(set(), storage.free_bytes_under_cap)
    assert evicted == []
    assert len(storage.clips.held_clips()) == 1


def test_near_the_cap_archived_clips_go_oldest_first(tmp_path):
    storage = Storage(tmp_path / "w", cap_bytes=100 * GB, disk_free=lambda _: 500 * GB)
    old_sha = keep_clip(storage, JOB_A, CLIP, age_seconds=3600)
    new_sha = keep_clip(storage, JOB_B, b"another clip " * 3000)

    # Pretend each clip is 2 GB and 3 GB are free under the cap now: one
    # eviction brings it to 5 GB, above the 4 GB headroom, so it stops there.
    def free_bytes_under_cap():
        return 3 * GB + 2 * GB * (2 - len(storage.clips.held_clips()))

    evicted = storage.clips.evict_archived({old_sha, new_sha}, free_bytes_under_cap)

    # Both were archived, but only the older one had to go.
    assert [clip.sha256 for clip in evicted] == [old_sha]
    assert [clip.sha256 for clip in storage.clips.held_clips()] == [new_sha]


def test_with_room_to_spare_nothing_is_evicted(tmp_path):
    storage = Storage(tmp_path / "w", cap_bytes=100 * GB, disk_free=lambda _: 500 * GB)
    sha256 = keep_clip(storage, JOB_A, CLIP)
    assert storage.clips.evict_archived({sha256}, storage.free_bytes_under_cap) == []


def test_the_heartbeat_evicts_only_what_the_orchestrator_says_is_archived(tmp_path):
    # Setup: a worker near its cap keeping two clips; the master archived one.
    harness = build_harness(tmp_path, cap_bytes=3 * GB)
    archived_sha = keep_clip(harness.storage, JOB_A, CLIP, age_seconds=60)
    unarchived_sha = keep_clip(harness.storage, JOB_B, b"not archived yet " * 1000)
    harness.orchestrator.archived_clip_sha256s = {archived_sha}

    harness.heartbeater.beat_once()

    # The archived clip went; the other stays however full the disk is.
    assert [c.sha256 for c in harness.storage.clips.held_clips()] == [unarchived_sha]
    assert harness.heartbeater.clips_archived_by_master == 1


# --- archive_clip on the master ---


def archive_task(job_id, sha256, urls=(), path="enduro/42/3.mp4", version=1):
    return ArchiveClipTask(
        job_id=job_id, version=version, sha256=sha256, size_bytes=len(CLIP),
        archive_relative_path=path, download_urls=list(urls),
    )  # fmt: skip


def test_the_master_moves_its_own_clip_into_the_archive(tmp_path):
    harness = build_harness(tmp_path, role=WorkerRole.MASTER)
    sha256 = keep_clip(harness.storage, JOB_A, CLIP)

    harness.claim_and_run(archive_task(JOB_A, sha256))

    # Mirrors the box layout; the flat kept copy is gone (moved, not copied).
    archived = harness.storage.clips_dir / "enduro" / "42" / "3.mp4"
    assert archived.read_bytes() == CLIP
    assert harness.storage.clips.held_clips() == []
    # And the orchestrator was told, with the exact hash.
    [(job_id, request)] = harness.orchestrator.clip_archived
    assert (job_id, request.sha256) == (JOB_A, sha256)


def test_the_master_downloads_a_clip_a_volunteer_kept(tmp_path):
    harness = build_harness(tmp_path, role=WorkerRole.MASTER)
    volunteer = Storage(tmp_path / "volunteer", 100 * GB, disk_free=lambda _: 500 * GB)
    sha256 = keep_clip(volunteer, JOB_A, CLIP)
    harness.network.add_app(PEER_URL, build_peer_file_server_app(volunteer, "v-1", "worker"))
    url = f"{PEER_URL}/clip/{JOB_A}?sha256={sha256}"

    harness.claim_and_run(archive_task(JOB_A, sha256, [url], path="enduro/42/3.v2.mp4", version=2))

    # Version 2 lands next to version 1's name, never on top of it.
    assert (harness.storage.clips_dir / "enduro" / "42" / "3.v2.mp4").read_bytes() == CLIP
    assert harness.orchestrator.clip_archived[0][1].sha256 == sha256
    # The volunteer's copy is untouched (it evicts only after the archive is confirmed).
    assert volunteer.clips.find(JOB_A, sha256) is not None


def test_no_reachable_source_reports_nothing_and_lets_the_lease_expire(tmp_path):
    # The master is the only machine that archives: a "worker" failure
    # report would only get in the way, so a network failure is left to the
    # lease (the orchestrator offers the clip again later).
    harness = build_harness(tmp_path, role=WorkerRole.MASTER)
    task = archive_task(JOB_A, CLIP_SHA, [f"{PEER_URL}/clip/{JOB_A}?sha256={CLIP_SHA}"])

    harness.claim_and_run(task)

    assert harness.orchestrator.clip_archived == []
    assert harness.orchestrator.failures == []
    assert harness.storage.clips.archived_clip_count() == 0


def test_an_unsafe_archive_path_is_refused(tmp_path):
    storage = Storage(tmp_path / "w", 10 * GB, disk_free=lambda _: 500 * GB)
    for bad in ("../outside.mp4", "/etc/x.mp4", "enduro/42/3.txt"):
        try:
            storage.clips.archive_path(bad)
        except ValueError:
            continue
        raise AssertionError(f"{bad} was accepted")
