"""Delivery to the studio: idempotent, lock-tolerant, fast fallback (LIVESTACK-41 item 13).

Seen live: "BAJALIVE falhou ao entregar o clipe ao estúdio: … [WinError 5]
Access is denied: '…\\1\\1.mp4.part' -> '…\\1\\1.mp4'", while the clip was
already in the studio folder. On Windows a rename onto a file that OBS/VLC
(or Defender, or the search indexer) holds open fails.

Linux lets you rename over an open file, so these tests play Windows with
`WindowsLikeFiles`: a replace() that refuses destinations marked "open"
(PermissionError, like WinError 5/32), and can also refuse a few times and
then let go (an antivirus scan). The cases:
  * destination locked + same content → success, nothing renamed;
  * destination locked + different content → the new version goes beside
    it (…-v2.mp4), latest.m3u names that one, the old file stays until it
    is released;
  * a transient lock → retried, lands at the normal name;
  * a double delivery (push twice, or push then outbox pull) → one file,
    one ACK;
  * an unreachable studio → the outbox within seconds, and the next clip
    does not even try the studio.
"""

import os
import time
from pathlib import Path

from shared.hashing import sha256_of_bytes
from shared.layout import LATEST_PLAYLIST_NAME
from shared.models import DeliverTask, JobState, WorkerRole
from tests.worker.conftest import STUDIO_URL, build_harness, direct_delivery
from tests.worker.fake_orchestrator import BASE_URL
from tests.worker.test_process import hold_raw, process_task
from tests.worker.test_studio_receiver import push, studio  # noqa: F401 (fixture)
from worker.studio.library import StudioLibrary
from worker.studio.placement import ClipHashCache, ClipPlacer

CLIP_PATH = "enduro/42/enduro-042-p3.mp4"
ON_AIR = b"version 1, playing in OBS " * 400
REPROCESSED = b"version 2, after a reprocess " * 400


class WindowsLikeFiles:
    """os.replace as Windows does it: an open destination cannot be replaced."""

    def __init__(self) -> None:
        self.open_files: set[Path] = set()
        # Destination → how many more replace() calls fail before it lets go.
        self.transient_locks: dict[Path, int] = {}
        self.replace_calls: list[Path] = []
        self.pauses: list[float] = []

    def replace(self, source: Path, destination: Path) -> None:
        self.replace_calls.append(destination)
        if destination in self.open_files:
            raise PermissionError(13, "[WinError 5] Access is denied", str(destination))
        if self.transient_locks.get(destination, 0) > 0:
            self.transient_locks[destination] -= 1
            raise PermissionError(13, "[WinError 32] used by another process", str(destination))
        os.replace(source, destination)

    def sleep(self, seconds: float) -> None:
        self.pauses.append(seconds)  # no real waiting in tests


class CountingHash:
    """sha256_of_file, counting how often a file is really read."""

    def __init__(self) -> None:
        self.reads = 0

    def __call__(self, path: Path) -> str:
        self.reads += 1
        return sha256_of_bytes(path.read_bytes())


def library_on(root: Path, files: WindowsLikeFiles, hashing=None) -> StudioLibrary:
    hashes = ClipHashCache(root, hash_file=hashing or CountingHash())
    placer = ClipPlacer(root, hashes, replace=files.replace, sleep=files.sleep)
    return StudioLibrary(root, placer=placer)


def staged_copy(library: StudioLibrary, content: bytes) -> Path:
    """A verified clip waiting in a hidden file beside its destination."""
    staged = library.root / "enduro/42/.incoming.mp4"
    staged.parent.mkdir(parents=True, exist_ok=True)
    staged.write_bytes(content)
    return staged


def on_air(library: StudioLibrary, files: WindowsLikeFiles, content: bytes = ON_AIR) -> Path:
    """The clip already in the folder, open in OBS."""
    path = library.root / CLIP_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    files.open_files.add(path)
    return path


# --- placing a clip --------------------------------------------------------------


def test_locked_destination_with_the_same_content_is_simply_delivered(tmp_path):
    files = WindowsLikeFiles()
    library = library_on(tmp_path / "box", files)
    existing = on_air(library, files)
    staged = staged_copy(library, ON_AIR)

    stored_as = library.place_clip(staged, CLIP_PATH, sha256_of_bytes(ON_AIR))

    # Success, at the normal name: no rename was even tried (so no WinError
    # 5), the incoming copy is gone, the clip on air untouched.
    assert stored_as == CLIP_PATH
    assert files.replace_calls == []
    assert not staged.exists()
    assert existing.read_bytes() == ON_AIR


def test_locked_destination_with_new_content_goes_beside_it_as_v2(tmp_path):
    files = WindowsLikeFiles()
    library = library_on(tmp_path / "box", files)
    old = on_air(library, files)
    library.record_clip(CLIP_PATH, None)

    stored_as = library.place_clip(
        staged_copy(library, REPROCESSED), CLIP_PATH, sha256_of_bytes(REPROCESSED)
    )

    # The reprocessed clip is next to the one on air, never over it...
    assert stored_as == "enduro/42/enduro-042-p3-v2.mp4"
    assert (library.root / stored_as).read_bytes() == REPROCESSED
    assert old.read_bytes() == ON_AIR
    # ...after a few short retries (a lock may be an antivirus scan)...
    assert files.pauses == [0.2, 0.5, 1.0, 2.0]
    # ...and latest.m3u now plays the new version only. (What happens to the
    # old file is the next test: Linux would let us delete it while "open".)
    library.record_clip(stored_as, None)
    playlist = (library.root / LATEST_PLAYLIST_NAME).read_text()
    assert stored_as in playlist and f"{CLIP_PATH}\n" not in playlist


def test_the_old_version_is_deleted_once_nothing_holds_it(tmp_path):
    files = WindowsLikeFiles()
    library = library_on(tmp_path / "box", files)
    old = on_air(library, files)
    stored_as = library.place_clip(
        staged_copy(library, REPROCESSED), CLIP_PATH, sha256_of_bytes(REPROCESSED)
    )
    # The real filesystem is Linux here: make unlink() fail like Windows too.
    real_unlink = Path.unlink

    def windows_unlink(path, missing_ok=False):
        if path in files.open_files:
            raise PermissionError(13, "in use", str(path))
        return real_unlink(path, missing_ok=missing_ok)

    Path.unlink = windows_unlink
    try:
        library.record_clip(stored_as, None)
        assert old.exists()  # still on air: kept
        files.open_files.clear()  # OBS moved on to the new clip
        library.record_clip(stored_as, None)  # any later clip in that folder
    finally:
        Path.unlink = real_unlink
    assert not old.exists()
    assert (library.root / stored_as).exists()


def test_a_transient_lock_is_retried_and_lands_at_the_normal_name(tmp_path):
    files = WindowsLikeFiles()
    library = library_on(tmp_path / "box", files)
    destination = library.root / CLIP_PATH
    files.transient_locks[destination] = 2  # Defender scans it for a moment

    stored_as = library.place_clip(
        staged_copy(library, REPROCESSED), CLIP_PATH, sha256_of_bytes(REPROCESSED)
    )

    assert stored_as == CLIP_PATH
    assert destination.read_bytes() == REPROCESSED
    assert files.pauses == [0.2, 0.5]


def test_a_big_clip_is_not_hashed_again_on_every_delivery(tmp_path):
    files = WindowsLikeFiles()
    hashing = CountingHash()
    library = library_on(tmp_path / "box", files, hashing)
    on_air(library, files)

    for _ in range(3):
        library.find_same_content(CLIP_PATH, sha256_of_bytes(ON_AIR))

    # Read once; afterwards the cached (size, mtime) answer is used, also
    # by a new library (a restarted studio).
    assert hashing.reads == 1
    again = library_on(tmp_path / "box", files, hashing)
    again.find_same_content(CLIP_PATH, sha256_of_bytes(ON_AIR))
    assert hashing.reads == 1


# --- double deliveries --------------------------------------------------------------


def test_the_same_clip_pushed_twice_is_one_file_and_one_ack(studio, tmp_path):  # noqa: F811
    orchestrator, library, acknowledger, client = studio
    job = orchestrator.add_job(b"raw", prova="Enduro", car_number=42, passagem=3)

    push(client, tmp_path, ON_AIR, CLIP_PATH, job_id=job.job_id)
    acknowledger.handle_all_pending()
    # The worker pushes again (it never saw our "complete", or retried).
    push(client, tmp_path, ON_AIR, CLIP_PATH, job_id=job.job_id)
    acknowledger.handle_all_pending()

    files = [p.name for p in (library.root / "enduro/42").iterdir() if p.suffix == ".mp4"]
    assert files == ["enduro-042-p3.mp4"]
    assert len(orchestrator.delivered) == 1


def test_an_outbox_pull_of_a_clip_already_pushed_downloads_nothing_and_acks(tmp_path):
    # The race behind the live error: the push reached the studio, but its
    # ACK was late, so the worker also uploaded the clip to the outbox and
    # the studio was offered it again. Now: no download, no rename, ACK.
    harness = build_harness(tmp_path, role=WorkerRole.STUDIO)
    job = harness.orchestrator.add_job(b"raw", prova="Enduro", car_number=42, passagem=3)
    harness.orchestrator.outbox[f"{job.job_id}.mp4"] = ON_AIR
    already = harness.env.studio_library.root / CLIP_PATH
    already.parent.mkdir(parents=True)
    already.write_bytes(ON_AIR)

    harness.claim_and_run(
        DeliverTask(
            job_id=job.job_id,
            download_url=f"{BASE_URL}/outbox/{job.job_id}.mp4",
            sha256=sha256_of_bytes(ON_AIR),
            size_bytes=len(ON_AIR),
            clip_relative_path=CLIP_PATH,
        )
    )

    assert harness.orchestrator.file_requests == []  # nothing downloaded
    [(_, ack)] = harness.orchestrator.outbox_acks
    assert ack.relative_path == CLIP_PATH
    assert harness.orchestrator.failures == []


# --- the pushing worker -----------------------------------------------------------------


def test_an_unreachable_studio_means_the_outbox_within_seconds(tmp_path, raw_bytes):
    # The offer says "direct" but nothing answers at the studio's address.
    harness = build_harness(tmp_path)
    job = hold_raw(harness, raw_bytes)

    started = time.monotonic()
    harness.claim_and_run(process_task(job, delivery=direct_delivery(job.job_id)))

    assert time.monotonic() - started < 5
    assert harness.orchestrator.stored_upload("outbox", job.job_id).exists()
    studio_requests = [url for url in harness.network.requested_urls if "studio.test" in url]
    # Only the 3-second check; no push was attempted.
    assert studio_requests == [f"{STUDIO_URL}/health"]

    # The next clip does not ask again: the studio is remembered as
    # unreachable for a few minutes.
    second = hold_raw(harness, raw_bytes + b"2")
    harness.claim_and_run(process_task(second, delivery=direct_delivery(second.job_id)))
    studio_requests = [url for url in harness.network.requested_urls if "studio.test" in url]
    assert studio_requests == [f"{STUDIO_URL}/health"]


def test_a_late_studio_ack_found_on_the_job_means_no_outbox_upload(tmp_path, raw_bytes):
    # The push worked, the lease was still renewed after the wait (the ACK
    # was late), but by then the job says "done" with our clip: uploading to
    # the outbox would only deliver it a second time.
    harness = build_harness(tmp_path)
    job = hold_raw(harness, raw_bytes)
    harness.add_studio()  # receives the push; its ACK is never sent here
    # What the fake ffmpeg writes for a final render (tests/worker/fakes.py).
    rendered_sha = sha256_of_bytes(b"fake final render output " * 1000)
    job.state, job.clip_sha256 = JobState.DONE, rendered_sha

    harness.claim_and_run(process_task(job, delivery=direct_delivery(job.job_id)))

    assert not harness.orchestrator.stored_upload("outbox", job.job_id).exists()
    assert harness.orchestrator.failures == []
