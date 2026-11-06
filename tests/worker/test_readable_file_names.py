"""Readable file names on the worker's disk (LIVESTACK-41 item 12).

Raws in raws/ are now called "<sender>-<YYYYMMDD-HHMMSS>-<job id first 8>.<ext>"
(the name comes in the download_raw offer), and clips kept in clips/ start
with "<prova>-<car>-p<passagem>". The code still looks everything up by job
id, so these tests check that:
  * a raw stored under its readable name is found, listed in heartbeats,
    resumed, served and deleted by job id, also after a restart;
  * a worker that already holds raws under the old "<job_id>.<ext>" name
    keeps finding them after the upgrade;
  * kept clips get the readable prefix and are still found by job and hash.
"""

from worker.clip_store import ClipStore
from worker.storage import RAW_NAMES_INDEX, STALE_PARTIAL_AGE_SECONDS, Storage

GB = 1024**3
JOB = "2f2510a3c0de4b1e9f00112233445566"
OLD_JOB = "0123456789abcdef0123456789abcdef"
READABLE = "joao-guilherme-bresolin-20261104-232909-2f2510a3.mp4"
SHA = "a" * 64


def storage_in(tmp_path) -> Storage:
    return Storage(tmp_path / "work", 100 * GB, disk_free=lambda _: 500 * GB)


def test_a_raw_with_a_readable_name_is_found_by_its_job_id(tmp_path):
    storage = storage_in(tmp_path)

    path = storage.raw_path(JOB, ".mp4", READABLE)
    path.write_bytes(b"raw")

    assert path.name == READABLE
    assert storage.find_raw(JOB) == path
    # Heartbeats list the full id (the orchestrator knows nothing else).
    assert storage.held_job_ids() == [JOB]


def test_the_name_to_id_map_survives_a_restart(tmp_path):
    storage_in(tmp_path).raw_path(JOB, ".mp4", READABLE).write_bytes(b"raw")

    # A new Storage = the worker started again (reboot, upgrade).
    again = storage_in(tmp_path)

    assert again.find_raw(JOB).name == READABLE
    assert again.held_job_ids() == [JOB]


def test_raws_named_before_the_upgrade_are_still_found(tmp_path):
    # The worker held this raw before item 12: "<job_id>.<ext>", no map entry.
    storage = storage_in(tmp_path)
    old = storage.raws_dir / f"{OLD_JOB}.mov"
    old.write_bytes(b"old raw")
    storage.raw_path(JOB, ".mp4", READABLE).write_bytes(b"new raw")

    assert storage.find_raw(OLD_JOB) == old
    assert storage.held_job_ids() == sorted([JOB, OLD_JOB])


def test_an_interrupted_download_under_a_readable_name_resumes(tmp_path):
    # The map is written when the download starts, so the hidden partial
    # file is found again by job id after a crash.
    storage = storage_in(tmp_path)
    destination = storage.raw_path(JOB, ".mp4", READABLE)
    (storage.raws_dir / f".{READABLE}.part").write_bytes(b"half")

    assert storage_in(tmp_path).partial_raw_destination(JOB) == destination
    assert storage.find_raw(JOB) is None  # not complete yet


def test_deleting_a_discarded_raw_removes_file_partials_and_map_entry(tmp_path):
    storage = storage_in(tmp_path)
    storage.raw_path(JOB, ".mp4", READABLE).write_bytes(b"raw")
    (storage.raws_dir / f".{READABLE}.part.json").write_text("{}")

    assert storage.delete_raw(JOB) is True

    assert [p.name for p in storage.raws_dir.iterdir()] == [RAW_NAMES_INDEX]
    assert JOB not in storage_in(tmp_path).raws_dir.joinpath(RAW_NAMES_INDEX).read_text()


def test_the_start_up_cleanup_never_deletes_the_map(tmp_path):
    # Hidden files older than a day are stale partial downloads and go;
    # the map is hidden too but must stay.
    storage = storage_in(tmp_path)
    storage.raw_path(JOB, ".mp4", READABLE).write_bytes(b"raw")
    later = (storage.raws_dir / RAW_NAMES_INDEX).stat().st_mtime + STALE_PARTIAL_AGE_SECONDS + 60

    storage.clean_up_at_start(now=later)

    assert storage.find_raw(JOB) is not None


def test_an_offered_name_that_is_a_path_is_ignored(tmp_path):
    # The name comes from the orchestrator; it must still never pick a folder.
    storage = storage_in(tmp_path)
    for bad in ("../evil.mp4", "sub/dir.mp4", ".hidden.mp4", "C:\\x.mp4"):
        assert storage.raw_path(JOB, ".mp4", bad) == storage.raws_dir / f"{JOB}.mp4"


def test_kept_clips_start_with_the_readable_clip_name(tmp_path):
    clips = ClipStore(tmp_path)
    rendered = tmp_path / f"{JOB}.mp4"
    rendered.write_bytes(b"clip")

    kept = clips.keep(rendered, JOB, SHA, "enduro-042-p3")

    assert kept.name == f"enduro-042-p3.{JOB}.{SHA}.mp4"
    assert clips.find(JOB, SHA) == kept
    assert [(c.job_id, c.sha256) for c in clips.held_clips()] == [(JOB, SHA)]


def test_clips_kept_before_the_upgrade_are_still_found(tmp_path):
    clips = ClipStore(tmp_path)
    old = tmp_path / f"{OLD_JOB}.{SHA}.mp4"
    old.write_bytes(b"clip")
    assert clips.find(OLD_JOB) == old
    assert clips.find(OLD_JOB, SHA) == old
