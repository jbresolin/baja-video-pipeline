"""upload_clip: a PC sends a finished clip a reviewer asked for ("Baixar clipe", LIVESTACK-52).

The orchestrator offers this task to a PC that has the clip: the master
(its archive), the worker that rendered it (its kept clips), or the studio
(its clip folder). The PC finds the clip by its SHA-256 and uploads it in
chunks under the task's lease. These tests run the real task against the
in-memory fake orchestrator (tests/worker/fake_orchestrator.py).
"""

from shared.hashing import sha256_of_bytes
from shared.models import TaskKind, UploadClipTask, WorkerRole
from tests.worker.conftest import build_harness
from tests.worker.fake_orchestrator import BASE_URL
from worker.dispatcher import ASK_AGAIN_FOR_REFUSED_KIND_SECONDS
from worker.stages import ClaimedTask

CLIP = b"a finished clip " * 2000
OTHER_CLIP = b"another clip " * 2000
JOB_ID = "0123456789abcdef0123456789abcdef"


def clip_task(clip: bytes = CLIP, **changes) -> UploadClipTask:
    fields = dict(
        job_id=JOB_ID,
        version=2,
        sha256=sha256_of_bytes(clip),
        size_bytes=len(clip),
        archive_relative_path="enduro/42/enduro-042-p3.v2.mp4",
        studio_relative_path="enduro/42/enduro-042-p3-v2.mp4",
        upload_url=f"{BASE_URL}/jobs/{JOB_ID}/clip-download-upload",
    )
    fields.update(changes)
    return UploadClipTask(**fields)


def uploaded(harness) -> bytes:
    return harness.orchestrator.stored_upload("requested_clips", JOB_ID).read_bytes()


def test_the_renderer_uploads_the_clip_it_kept(harness, tmp_path):
    # After a delivery every worker keeps its clip (worker.clip_store).
    rendered = tmp_path / "rendered.mp4"
    rendered.write_bytes(CLIP)
    harness.storage.clips.keep(rendered, JOB_ID, sha256_of_bytes(CLIP), "enduro-042-p3")

    harness.claim_and_run(clip_task())

    assert uploaded(harness) == CLIP
    assert harness.orchestrator.failures == []
    # The kept clip stays: the VPS copy is only for the reviewer.
    assert harness.storage.clips.find(JOB_ID) is not None


def test_the_master_uploads_the_copy_in_its_archive(tmp_path):
    master = build_harness(tmp_path, role=WorkerRole.MASTER)
    archived = master.storage.clips.archive_path("enduro/42/enduro-042-p3.v2.mp4")
    archived.parent.mkdir(parents=True)
    archived.write_bytes(CLIP)

    master.claim_and_run(clip_task())

    assert uploaded(master) == CLIP


def test_an_archive_file_with_other_content_is_not_sent(tmp_path):
    # The archive has a file at that path, but not the clip asked for
    # (checked by its SHA-256): nothing wrong goes to the reviewer.
    master = build_harness(tmp_path, role=WorkerRole.MASTER)
    archived = master.storage.clips.archive_path("enduro/42/enduro-042-p3.v2.mp4")
    archived.parent.mkdir(parents=True)
    archived.write_bytes(OTHER_CLIP)

    master.claim_and_run(clip_task())

    [(_, failure)] = master.orchestrator.failures
    assert failure.scope == "worker"


def test_the_studio_uploads_the_clip_from_its_folder(tmp_path):
    # The studio stored version 2 as "-v3" (version 2's name was locked on
    # air): it is found by content under any version name of the passagem.
    studio = build_harness(tmp_path, role=WorkerRole.STUDIO)
    library = studio.env.studio_library
    stored = library.absolute_path("enduro/42/enduro-042-p3-v3.mp4")
    stored.parent.mkdir(parents=True)
    stored.write_bytes(CLIP)
    library.absolute_path("enduro/42/enduro-042-p3.mp4").write_bytes(OTHER_CLIP)

    studio.claim_and_run(clip_task())

    assert uploaded(studio) == CLIP


def test_a_clip_missing_from_this_pc_is_reported_so_the_next_pc_is_asked(harness):
    harness.claim_and_run(clip_task())

    [(_, failure)] = harness.orchestrator.failures
    # Scope "worker": this PC is left out of the task and another one is asked.
    assert failure.scope == "worker"
    assert "not on this PC" in failure.message


def test_every_role_asks_for_it_and_the_studio_only_while_not_delivering(tmp_path):
    for role in (WorkerRole.MASTER, WorkerRole.WORKER):
        worker = build_harness(tmp_path / role, role=role)
        assert TaskKind.UPLOAD_CLIP in worker.dispatcher.kinds_to_ask_for()

    studio = build_harness(tmp_path / "studio", role=WorkerRole.STUDIO)
    assert studio.dispatcher.kinds_to_ask_for() == [TaskKind.DELIVER, TaskKind.UPLOAD_CLIP]
    # While its deliver stage pulls a clip from the outbox, the studio asks
    # for nothing else: clips for the air come first.
    studio.stages[TaskKind.DELIVER].submit(ClaimedTask(lease=None, task=None))
    assert TaskKind.UPLOAD_CLIP not in studio.dispatcher.kinds_to_ask_for()


def test_an_orchestrator_without_upload_clip_does_not_block_other_work(harness):
    # Deployment order is VPS first, but a 0.7.0 worker may still meet an
    # older orchestrator, which answers 400 to any /next naming upload_clip.
    harness.orchestrator.unknown_task_kinds = {"upload_clip"}

    harness.dispatcher.run_once()  # refused once...
    harness.dispatcher.run_once()  # ...then asks without it, and is answered

    last = harness.orchestrator.next_requests[-1]
    assert TaskKind.UPLOAD_CLIP not in last.accept_kinds
    assert TaskKind.PROCESS in last.accept_kinds
    # Five minutes later it tries again (the VPS may have been updated).
    harness.orchestrator.unknown_task_kinds = set()
    harness.steady_clock.advance(ASK_AGAIN_FOR_REFUSED_KIND_SECONDS)
    harness.dispatcher.run_once()
    assert TaskKind.UPLOAD_CLIP in harness.orchestrator.next_requests[-1].accept_kinds
