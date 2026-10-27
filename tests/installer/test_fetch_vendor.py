"""installer/fetch_vendor.py: download, verify the SHA-256, unpack.

No network: every "download" is a file:// URL pointing at a file the test
creates in pytest's temporary folder (`tmp_path`). urllib reads file://
URLs the same way it reads https:// ones, so the real code path runs.
"""

import hashlib
import zipfile

import pytest

from tests.installer.installer_scripts import load_installer_script

fetch_vendor = load_installer_script("fetch_vendor")


def make_source_file(folder, name, content: bytes):
    """Write `content` to folder/name; return (file URL, its SHA-256)."""
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_bytes(content)
    return path.as_uri(), hashlib.sha256(content).hexdigest()


def make_zip(folder, name, members: dict[str, bytes]):
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    with zipfile.ZipFile(path, "w") as zipped:
        for member, content in members.items():
            zipped.writestr(member, content)
    return path.as_uri(), hashlib.sha256(path.read_bytes()).hexdigest()


def test_a_file_with_the_right_hash_is_kept_under_its_real_name(tmp_path):
    # Set up: a "remote" MSI and a lock entry with its correct hash.
    url, digest = make_source_file(tmp_path / "remote", "tool.msi", b"msi bytes")
    payload = fetch_vendor.Payload(name="tool", url=url, sha256=digest)
    vendor = tmp_path / "vendor"

    used = fetch_vendor.fetch_payload(payload, vendor, say=lambda line: None)

    # The file is in vendor/downloads/ with its original name and content,
    # and no ".part" leftover exists.
    assert used == vendor / "downloads" / "tool.msi"
    assert used.read_bytes() == b"msi bytes"
    assert [p.name for p in used.parent.iterdir()] == ["tool.msi"]


def test_a_hash_mismatch_stops_the_build_and_leaves_no_file(tmp_path):
    # Set up: the lock expects a different hash than the file really has,
    # as if the download had been tampered with or the release replaced.
    url, _ = make_source_file(tmp_path / "remote", "tool.msi", b"unexpected bytes")
    payload = fetch_vendor.Payload(name="tool", url=url, sha256="0" * 64)
    vendor = tmp_path / "vendor"

    with pytest.raises(fetch_vendor.VendorError, match="SHA-256 mismatch"):
        fetch_vendor.fetch_payload(payload, vendor, say=lambda line: None)

    # Nothing is left behind that a later build step could pick up.
    assert list((vendor / "downloads").iterdir()) == []


def test_a_verified_download_is_not_fetched_again(tmp_path):
    url, digest = make_source_file(tmp_path / "remote", "tool.msi", b"msi bytes")
    payload = fetch_vendor.Payload(name="tool", url=url, sha256=digest)
    vendor = tmp_path / "vendor"
    fetch_vendor.fetch_payload(payload, vendor, say=lambda line: None)

    # Delete the "remote" copy: a second run must not need it, because the
    # local file is already there and its hash still matches.
    (tmp_path / "remote" / "tool.msi").unlink()
    messages = []
    fetch_vendor.fetch_payload(payload, vendor, say=messages.append)

    assert messages == ["tool: already downloaded and verified"]


def test_a_corrupted_local_copy_is_downloaded_again(tmp_path):
    url, digest = make_source_file(tmp_path / "remote", "tool.msi", b"msi bytes")
    payload = fetch_vendor.Payload(name="tool", url=url, sha256=digest)
    vendor = tmp_path / "vendor"
    fetch_vendor.fetch_payload(payload, vendor, say=lambda line: None)
    # Damage the local copy, as an interrupted copy or a disk error would.
    (vendor / "downloads" / "tool.msi").write_bytes(b"damaged")

    fetch_vendor.fetch_payload(payload, vendor, say=lambda line: None)

    assert (vendor / "downloads" / "tool.msi").read_bytes() == b"msi bytes"


def test_a_zip_is_unpacked_and_the_build_gets_the_path_inside_it(tmp_path):
    # Set up: a zip shaped like BtbN's (a top folder holding bin/).
    url, digest = make_zip(
        tmp_path / "remote",
        "ffmpeg-x-win64.zip",
        {"ffmpeg-x-win64/bin/ffmpeg.exe": b"exe", "ffmpeg-x-win64/LICENSE.txt": b"GPL"},
    )
    payload = fetch_vendor.Payload(
        name="ffmpeg", url=url, sha256=digest, path_in_archive="ffmpeg-x-win64"
    )

    used = fetch_vendor.fetch_payload(payload, tmp_path / "vendor", say=lambda line: None)

    # The returned path is vendor/ffmpeg/<path_in_archive>, with bin/ inside.
    assert used == tmp_path / "vendor" / "ffmpeg" / "ffmpeg-x-win64"
    assert (used / "bin" / "ffmpeg.exe").read_bytes() == b"exe"


def test_a_zip_without_the_expected_path_is_refused(tmp_path):
    # If a future zip is laid out differently, fail now, not in the build.
    url, digest = make_zip(tmp_path / "remote", "nssm.zip", {"other/nssm.exe": b"exe"})
    payload = fetch_vendor.Payload(
        name="nssm", url=url, sha256=digest, path_in_archive="nssm-2.24/win64/nssm.exe"
    )

    with pytest.raises(fetch_vendor.VendorError, match="not found in the zip"):
        fetch_vendor.fetch_payload(payload, tmp_path / "vendor", say=lambda line: None)


@pytest.mark.parametrize("evil_member", ["../outside.txt", "/etc/evil", "C:/evil.dll"])
def test_zip_members_that_would_escape_the_vendor_folder_are_refused(tmp_path, evil_member):
    # A "zip slip" member names a path outside the destination folder;
    # extracting it would overwrite files elsewhere on the build machine.
    archive = tmp_path / "evil.zip"
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr(evil_member, b"x")

    with pytest.raises(fetch_vendor.VendorError, match="unsafe path"):
        fetch_vendor.extract_zip_safely(archive, tmp_path / "out")


def test_fetch_all_refuses_a_lock_missing_a_payload(tmp_path):
    # A lock with only ffmpeg: the build would produce an installer without
    # NSSM or Tailscale, so fetching must refuse before downloading anything.
    lock = tmp_path / "vendor.lock"
    lock.write_text(
        '[ffmpeg]\nurl = "https://example.invalid/a.zip"\nsha256 = "' + "0" * 64 + '"\n'
    )

    with pytest.raises(fetch_vendor.VendorError, match="nssm, tailscale"):
        fetch_vendor.fetch_all(lock, tmp_path / "vendor", say=lambda line: None)


def test_when_the_main_url_fails_a_mirror_with_the_same_hash_is_used(tmp_path):
    # Set up: the main URL points at a file that does not exist (like
    # nssm.cc answering 503); the mirror has the right bytes.
    good_url, digest = make_source_file(tmp_path / "mirror", "tool.msi", b"msi bytes")
    payload = fetch_vendor.Payload(
        name="tool",
        url=(tmp_path / "gone" / "tool.msi").as_uri(),
        sha256=digest,
        mirrors=(good_url,),
    )
    messages = []

    used = fetch_vendor.fetch_payload(
        payload,
        tmp_path / "vendor",
        say=messages.append,
        sleep=lambda seconds: None,  # don't really wait between retries
    )

    # The file came from the mirror, under the main URL's file name.
    assert used.read_bytes() == b"msi bytes"
    # The main URL was retried before giving up on it.
    assert sum("retrying" in line for line in messages) == fetch_vendor.ATTEMPTS_PER_SOURCE - 1


def test_a_mirror_with_the_wrong_bytes_is_never_kept(tmp_path):
    # Both sources answer, but neither has the pinned hash: nothing is kept
    # and the error lists both.
    first, _ = make_source_file(tmp_path / "a", "tool.msi", b"one")
    second, _ = make_source_file(tmp_path / "b", "tool.msi", b"two")
    payload = fetch_vendor.Payload(name="tool", url=first, sha256="0" * 64, mirrors=(second,))

    with pytest.raises(fetch_vendor.VendorError) as failure:
        fetch_vendor.fetch_payload(payload, tmp_path / "vendor", say=lambda line: None)

    assert str(failure.value).count("SHA-256 mismatch") == 2
    assert list((tmp_path / "vendor" / "downloads").iterdir()) == []


def test_every_mirror_in_the_lock_is_https():
    for payload in fetch_vendor.read_lock().values():
        assert all(mirror.startswith("https://") for mirror in payload.mirrors), payload.name
