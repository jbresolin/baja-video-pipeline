"""Storage: working folder layout, cap accounting, and what may be deleted (design §9)."""

import os
import time

from worker.storage import DISK_RESERVE_BYTES, Storage

GB = 1024**3


def make_storage(tmp_path, cap=10 * GB, disk_free=500 * GB, master=False) -> Storage:
    return Storage(
        tmp_path / "work", cap, ignores_cap_for_raws=master, disk_free=lambda _: disk_free
    )


def test_folders_are_created(tmp_path):
    storage = make_storage(tmp_path)
    assert {p.name for p in storage.root.iterdir()} == {"raws", "clips", "tmp"}


def test_every_file_counts_against_the_cap(tmp_path):
    storage = make_storage(tmp_path, cap=10_000)
    storage.raw_path("a", ".mp4").write_bytes(b"x" * 3000)
    storage.raws_dir.joinpath(".b.mp4.part").write_bytes(b"x" * 1000)  # partial download
    storage.clip_path("c").write_bytes(b"x" * 2000)
    storage.tmp_path("proxy.mp4").write_bytes(b"x" * 500)
    assert storage.used_bytes() == 6500
    assert storage.free_bytes_under_cap() == 3500


def test_free_bytes_is_limited_by_the_real_disk_too(tmp_path):
    # A 100 GB cap on a disk with only 5 GB free: the disk wins (minus a
    # reserve so Windows never runs out of space).
    storage = make_storage(tmp_path, cap=100 * GB, disk_free=5 * GB)
    assert storage.free_bytes_under_cap() == 5 * GB - DISK_RESERVE_BYTES


def test_volunteer_cannot_take_a_raw_over_the_cap(tmp_path):
    storage = make_storage(tmp_path, cap=1000)
    assert storage.can_hold_new_raw(1000)
    assert not storage.can_hold_new_raw(1001)


def test_master_ignores_the_cap_but_not_the_disk(tmp_path):
    storage = make_storage(tmp_path, cap=1000, disk_free=10 * GB, master=True)
    assert storage.can_hold_new_raw(5 * GB)
    assert not storage.can_hold_new_raw(9 * GB)  # 10 GB free minus the 2 GB reserve


def test_held_raws_are_never_evicted_even_over_the_cap(tmp_path):
    # Over the cap, the worker just stops taking new raws; nothing it holds
    # is deleted to make room (design §9).
    storage = make_storage(tmp_path, cap=100)
    storage.raw_path("a", ".mp4").write_bytes(b"x" * 500)
    storage.clean_up_at_start()
    assert storage.free_bytes_under_cap() == 0
    assert storage.held_job_ids() == ["a"]


def test_delete_raw_removes_complete_and_partial_files_of_that_job_only(tmp_path):
    storage = make_storage(tmp_path)
    storage.raw_path("a", ".mp4").write_bytes(b"x")
    storage.raws_dir.joinpath(".a.mp4.part.json").write_text("{}")
    storage.raw_path("ab", ".mp4").write_bytes(b"y")  # a different job whose id starts with "a"
    assert storage.delete_raw("a")
    assert [p.name for p in storage.raws_dir.iterdir()] == ["ab.mp4"]


def test_start_up_cleans_tmp_and_old_partials_but_keeps_clips_and_fresh_partials(tmp_path):
    storage = make_storage(tmp_path)
    storage.tmp_path("leftover.mp4").write_bytes(b"x")
    old_partial = storage.raws_dir / ".old.mp4.part"
    old_partial.write_bytes(b"x")
    two_days_ago = time.time() - 2 * 24 * 3600
    os.utime(old_partial, (two_days_ago, two_days_ago))
    fresh_partial = storage.raws_dir / ".fresh.mp4.part"
    fresh_partial.write_bytes(b"x")
    storage.clip_path("done").write_bytes(b"clip")

    storage.clean_up_at_start()

    assert list(storage.tmp_dir.iterdir()) == []
    assert not old_partial.exists()
    assert fresh_partial.exists()  # may still be resumed
    assert storage.clip_path("done").exists()  # may be reused after a crash
