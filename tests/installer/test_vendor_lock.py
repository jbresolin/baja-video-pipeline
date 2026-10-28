"""installer/vendor.lock: every bundled third-party file is pinned by URL and hash.

The lock is what stops a build from silently shipping a different ffmpeg,
NSSM or Tailscale than the one we reviewed. These tests read the real lock
file and check its shape; they do not download anything.
"""

import re

from tests.installer.installer_scripts import load_installer_script

fetch_vendor = load_installer_script("fetch_vendor")

SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")


def test_lock_pins_ffmpeg_nssm_and_tailscale():
    # read_lock parses the TOML file into one Payload per [section].
    payloads = fetch_vendor.read_lock()

    # All three payloads the installer needs must be there.
    assert set(fetch_vendor.REQUIRED_PAYLOADS) <= set(payloads)
    assert set(fetch_vendor.REQUIRED_PAYLOADS) == {"ffmpeg", "nssm", "tailscale"}


def test_every_hash_is_64_lowercase_hex_characters():
    # A SHA-256 written as hex is exactly 64 characters 0-9/a-f. A typo, a
    # truncated paste or an "sha256:" prefix would fail this.
    for payload in fetch_vendor.read_lock().values():
        assert SHA256_HEX.match(payload.sha256), payload.name


def test_every_url_is_https():
    # Plain HTTP would let anyone on the path swap the file (the hash would
    # still catch it, but the build should never even try).
    for payload in fetch_vendor.read_lock().values():
        assert payload.url.startswith("https://"), payload.name


def test_ffmpeg_is_a_pinned_btbn_win64_gpl_shared_build():
    ffmpeg = fetch_vendor.read_lock()["ffmpeg"]

    # BtbN publishes builds under release tags; "latest" moves every day,
    # so a pinned URL must name a fixed autobuild tag instead.
    assert ffmpeg.url.startswith("https://github.com/BtbN/FFmpeg-Builds/releases/download/")
    assert "/download/autobuild-" in ffmpeg.url
    assert "/latest/" not in ffmpeg.url
    # Windows 64-bit, GPL (has libx264), shared DLLs: what design §9 asks for.
    assert "win64-gpl-shared" in ffmpeg.file_name
    # The build copies <path_in_archive>/bin, so the lock must say where it is.
    assert ffmpeg.path_in_archive and ffmpeg.file_name.startswith(ffmpeg.path_in_archive)


def test_nssm_is_version_2_24_and_uses_the_64_bit_exe():
    nssm = fetch_vendor.read_lock()["nssm"]
    assert nssm.file_name == "nssm-2.24.zip"
    assert nssm.path_in_archive == "nssm-2.24/win64/nssm.exe"


def test_tailscale_is_the_stable_amd64_msi():
    tailscale = fetch_vendor.read_lock()["tailscale"]
    assert tailscale.url.startswith("https://pkgs.tailscale.com/stable/")
    assert re.fullmatch(r"tailscale-setup-\d+\.\d+\.\d+-amd64\.msi", tailscale.file_name)
    # The MSI is used as downloaded, not unpacked.
    assert tailscale.is_zip is False
