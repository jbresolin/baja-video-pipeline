"""Download the third-party payloads pinned in vendor.lock and verify them.

    python installer/fetch_vendor.py [--vendor-dir installer/vendor]

For each entry: download `url` to vendor/downloads/<file name>, compute the
SHA-256 while writing, and compare it with the lock. Only a file whose hash
matches is kept (under its real name); anything else is deleted and the
script exits non-zero. Zips are then unpacked into vendor/<entry name>/.
A download that is already present with the right hash is not fetched
again, so re-running is cheap.

Standard library only, so it runs before any dependency is installed.
"""

import argparse
import hashlib
import shutil
import sys
import tomllib
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

INSTALLER_FOLDER = Path(__file__).resolve().parent
DEFAULT_LOCK_PATH = INSTALLER_FOLDER / "vendor.lock"
DEFAULT_VENDOR_FOLDER = INSTALLER_FOLDER / "vendor"
DOWNLOADS_FOLDER_NAME = "downloads"
REQUIRED_PAYLOADS = ("ffmpeg", "nssm", "tailscale")
CHUNK_BYTES = 1024 * 1024
DOWNLOAD_TIMEOUT_SECONDS = 120


class VendorError(Exception):
    """A payload could not be fetched or did not match its pinned hash."""


@dataclass(frozen=True)
class Payload:
    name: str
    url: str
    sha256: str
    # Inside the unpacked zip: the folder or file the build uses. None for
    # payloads used as downloaded (the Tailscale MSI).
    path_in_archive: str | None = None

    @property
    def file_name(self) -> str:
        return PurePosixPath(self.url).name

    @property
    def is_zip(self) -> bool:
        return self.file_name.lower().endswith(".zip")


def read_lock(lock_path: Path = DEFAULT_LOCK_PATH) -> dict[str, Payload]:
    entries = tomllib.loads(lock_path.read_text(encoding="utf-8"))
    return {
        name: Payload(
            name=name,
            url=entry["url"],
            sha256=entry["sha256"].lower(),
            path_in_archive=entry.get("path_in_archive"),
        )
        for name, entry in entries.items()
    }


def downloaded_file(vendor_folder: Path, payload: Payload) -> Path:
    return vendor_folder / DOWNLOADS_FOLDER_NAME / payload.file_name


def payload_path(vendor_folder: Path, payload: Payload) -> Path:
    """Where the build finds the part of a payload it uses."""
    if payload.is_zip:
        return vendor_folder / payload.name / (payload.path_in_archive or "")
    return downloaded_file(vendor_folder, payload)


def sha256_of_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while chunk := file.read(CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def download_verified(payload: Payload, target: Path) -> None:
    """Download to `<target>.part`, check the hash, then rename to `target`.

    The partial name means a crash or a hash mismatch never leaves a file
    under the real name that a later step could mistake for a good one.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".part")
    digest = hashlib.sha256()
    try:
        with (
            urllib.request.urlopen(payload.url, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response,
            partial.open("wb") as file,
        ):
            while chunk := response.read(CHUNK_BYTES):
                digest.update(chunk)
                file.write(chunk)
    except OSError as error:  # URLError and HTTPError are OSErrors
        partial.unlink(missing_ok=True)
        raise VendorError(f"{payload.name}: download of {payload.url} failed: {error}") from error
    actual = digest.hexdigest()
    if actual != payload.sha256:
        partial.unlink(missing_ok=True)
        raise VendorError(
            f"{payload.name}: SHA-256 mismatch for {payload.url}\n"
            f"  expected {payload.sha256}\n  got      {actual}"
        )
    partial.replace(target)


def extract_zip_safely(archive: Path, destination: Path) -> None:
    """Unpack `archive` into a fresh `destination`.

    Refuses members with absolute paths or `..` parts ("zip slip"), which
    could otherwise write outside the vendor folder.
    """
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    with zipfile.ZipFile(archive) as zipped:
        for member in zipped.namelist():
            member_path = PurePosixPath(member.replace("\\", "/"))
            if member_path.is_absolute() or ".." in member_path.parts or ":" in member:
                raise VendorError(f"{archive.name}: unsafe path in zip: {member!r}")
        zipped.extractall(destination)


def fetch_payload(payload: Payload, vendor_folder: Path, say=print) -> Path:
    """Make sure the payload is downloaded, verified and unpacked. Returns
    the path the build uses (see payload_path)."""
    target = downloaded_file(vendor_folder, payload)
    if target.exists() and sha256_of_file(target) == payload.sha256:
        say(f"{payload.name}: already downloaded and verified")
    else:
        target.unlink(missing_ok=True)
        say(f"{payload.name}: downloading {payload.url}")
        download_verified(payload, target)
        say(f"{payload.name}: SHA-256 verified")
    if payload.is_zip:
        extract_zip_safely(target, vendor_folder / payload.name)
    used = payload_path(vendor_folder, payload)
    if not used.exists():
        raise VendorError(f"{payload.name}: {payload.path_in_archive!r} not found in the zip")
    return used


def fetch_all(lock_path: Path, vendor_folder: Path, say=print) -> dict[str, Path]:
    payloads = read_lock(lock_path)
    missing = [name for name in REQUIRED_PAYLOADS if name not in payloads]
    if missing:
        raise VendorError(f"vendor.lock has no entry for: {', '.join(missing)}")
    return {name: fetch_payload(payload, vendor_folder, say) for name, payload in payloads.items()}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--lock", type=Path, default=DEFAULT_LOCK_PATH)
    parser.add_argument("--vendor-dir", type=Path, default=DEFAULT_VENDOR_FOLDER)
    args = parser.parse_args(argv)
    try:
        paths = fetch_all(args.lock, args.vendor_dir)
    except VendorError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    for name, path in paths.items():
        print(f"{name}: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
