"""Client side of file transfers: resumable downloads and chunked uploads.

Both take an `httpx.Client`, so callers decide base URL, auth and timeouts,
and tests can pass an in-process client (no real network).

Download: bytes go to "<destination>.part". If that file already exists
(a previous attempt was cut off), we ask the server only for the missing
bytes with a `Range: bytes=<size>-` header. At the end the SHA-256 is
checked and only then is the file renamed to its final name, so a file
without ".part" is always complete and verified.

Upload: the file is sent in chunks, each a separate POST carrying the
Upload-Offset / Upload-Length / Upload-SHA256 headers (docs/api.md,
"Chunked uploads"). If the receiver already has some bytes it answers 409
with the offset it expects, and we continue from there.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import httpx

from shared.hashing import sha256_of_file
from shared.models.api_worker_loop import ChunkAccepted
from shared.models.common import ErrorCode
from shared.protocol import (
    DEFAULT_UPLOAD_CHUNK_BYTES,
    UPLOAD_LENGTH_HEADER,
    UPLOAD_OFFSET_HEADER,
    UPLOAD_SHA256_HEADER,
)


class TransferError(Exception):
    """Base class for every transfer failure raised by this module."""


class HashMismatchError(TransferError):
    def __init__(self, expected: str, actual: str | None) -> None:
        self.expected = expected
        self.actual = actual
        super().__init__(f"SHA-256 mismatch: expected {expected}, got {actual}")


class DownloadFailedError(TransferError):
    pass


class UploadFailedError(TransferError):
    pass


class _RetryableHttpError(TransferError):
    """A server error (5xx) or a confused response worth trying again."""


class _OffsetCorrectionError(TransferError):
    """The upload receiver wants a different offset; continue from `expected_offset`."""

    def __init__(self, expected_offset: int) -> None:
        self.expected_offset = expected_offset
        super().__init__(f"receiver expects offset {expected_offset}")


@dataclass(frozen=True)
class DownloadResult:
    path: Path
    size_bytes: int
    sha256: str
    # How many bytes were already on disk before this call (0 = fresh start).
    resumed_from_byte: int
    attempts: int


@dataclass(frozen=True)
class UploadResult:
    size_bytes: int
    sha256: str
    requests_sent: int


def partial_path_for(destination: Path) -> Path:
    """Where an unfinished download of `destination` is kept."""
    return destination.with_name(destination.name + ".part")


def download_file(
    client: httpx.Client,
    url: str,
    destination: Path,
    expected_sha256: str | None = None,
    *,
    headers: dict[str, str] | None = None,
    max_attempts: int = 5,
    retry_delay_s: float = 1.0,
    sleep: Callable[[float], None] = time.sleep,
) -> DownloadResult:
    """Download `url` to `destination`, resuming a previous partial download.

    Network errors and 5xx answers are retried up to `max_attempts` times,
    each retry continuing from the bytes already on disk. When
    `expected_sha256` is given and the finished file does not match, the
    partial file is deleted and HashMismatchError is raised.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial_path = partial_path_for(destination)
    resumed_from_byte = partial_path.stat().st_size if partial_path.exists() else 0

    attempts = 0
    while True:
        attempts += 1
        try:
            _fetch_missing_bytes(client, url, partial_path, headers or {})
            break
        except (httpx.TransportError, _RetryableHttpError) as error:
            if attempts >= max_attempts:
                raise DownloadFailedError(
                    f"giving up on {url} after {attempts} attempts: {error}"
                ) from error
            sleep(retry_delay_s)

    actual_sha256 = sha256_of_file(partial_path)
    if expected_sha256 is not None and actual_sha256 != expected_sha256:
        # A corrupt partial file would fail again on every resume, so drop it.
        partial_path.unlink()
        raise HashMismatchError(expected_sha256, actual_sha256)

    partial_path.replace(destination)
    return DownloadResult(
        path=destination,
        size_bytes=destination.stat().st_size,
        sha256=actual_sha256,
        resumed_from_byte=resumed_from_byte,
        attempts=attempts,
    )


def _fetch_missing_bytes(
    client: httpx.Client,
    url: str,
    partial_path: Path,
    headers: dict[str, str],
) -> None:
    """One attempt: append whatever the partial file is missing."""
    bytes_on_disk = partial_path.stat().st_size if partial_path.exists() else 0
    # "identity" = no gzip: byte offsets must refer to the file itself, not
    # to a compressed version of it.
    request_headers = {**headers, "Accept-Encoding": "identity"}
    if bytes_on_disk > 0:
        request_headers["Range"] = f"bytes={bytes_on_disk}-"

    with client.stream("GET", url, headers=request_headers) as response:
        if response.status_code == 416:
            # "Range Not Satisfiable": we asked to start at or past the end,
            # so the partial file already holds every byte. The hash check
            # in download_file decides whether those bytes are right.
            return
        if response.status_code >= 500:
            raise _RetryableHttpError(f"server answered HTTP {response.status_code}")
        if response.status_code == 206:
            _check_range_starts_at(response, bytes_on_disk, partial_path)
            write_mode = "ab"  # append the missing tail
        elif response.status_code == 200:
            write_mode = "wb"  # server ignored Range and sent everything: start over
        else:
            raise DownloadFailedError(f"GET {url} answered HTTP {response.status_code}")

        # Without a chunk size, iter_bytes() hands over bytes as soon as they
        # arrive (no buffering), so if the connection drops, everything
        # received so far is already on disk and the next attempt resumes
        # after it.
        with partial_path.open(write_mode) as partial_file:
            for received_bytes in response.iter_bytes():
                partial_file.write(received_bytes)


def _check_range_starts_at(response: httpx.Response, expected_start: int, partial: Path) -> None:
    """Make sure a 206 answer starts exactly where our partial file ends.

    Content-Range looks like "bytes 1000-4999/5000". Appending bytes that
    start elsewhere would silently corrupt the file.
    """
    content_range = response.headers.get("Content-Range", "")
    try:
        start_text = content_range.split()[1].split("-")[0]
        actual_start = int(start_text)
    except (IndexError, ValueError):
        actual_start = None
    if actual_start != expected_start:
        partial.unlink(missing_ok=True)
        raise _RetryableHttpError(
            f"asked for bytes from {expected_start}, server sent '{content_range}'"
        )


def upload_file_in_chunks(
    client: httpx.Client,
    url: str,
    source: Path,
    *,
    sha256: str | None = None,
    headers: dict[str, str] | None = None,
    chunk_size: int = DEFAULT_UPLOAD_CHUNK_BYTES,
    max_attempts: int = 5,
    retry_delay_s: float = 1.0,
    sleep: Callable[[float], None] = time.sleep,
) -> UploadResult:
    """POST `source` to `url` chunk by chunk until the receiver says complete.

    `sha256` can be passed when already known, to avoid hashing twice.
    Network errors, 5xx answers and offset corrections all count toward
    `max_attempts`; a 422 hash_mismatch raises HashMismatchError at once.
    """
    total_size = source.stat().st_size
    file_sha256 = sha256 or sha256_of_file(source)
    offset = 0
    requests_sent = 0
    problems = 0

    with source.open("rb") as file:
        while True:
            file.seek(offset)
            chunk = file.read(chunk_size)
            request_headers = {
                **(headers or {}),
                "Content-Type": "application/octet-stream",
                UPLOAD_OFFSET_HEADER: str(offset),
                UPLOAD_LENGTH_HEADER: str(total_size),
                UPLOAD_SHA256_HEADER: file_sha256,
            }
            requests_sent += 1
            try:
                response = client.post(url, content=chunk, headers=request_headers)
                next_offset = _interpret_chunk_response(response, file_sha256)
            except _OffsetCorrectionError as correction:
                # The receiver already has some bytes (or fewer than we
                # thought): jump to its offset straight away, no waiting.
                problems += 1
                if problems >= max_attempts:
                    raise UploadFailedError(f"{url} keeps correcting the offset") from correction
                offset = correction.expected_offset
                continue
            except (httpx.TransportError, _RetryableHttpError) as error:
                problems += 1
                if problems >= max_attempts:
                    raise UploadFailedError(
                        f"giving up on {url} after {problems} problems: {error}"
                    ) from error
                sleep(retry_delay_s)
                continue

            if next_offset is None:  # the receiver has the whole, verified file
                return UploadResult(total_size, file_sha256, requests_sent)
            offset = next_offset


def _interpret_chunk_response(response: httpx.Response, file_sha256: str) -> int | None:
    """Turn one chunk response into "next offset to send", or None when finished."""
    if response.status_code == 200:
        accepted = ChunkAccepted.model_validate(response.json())
        return None if accepted.complete else accepted.received_bytes

    error = _error_body(response)
    if response.status_code == 409 and error.get("code") == ErrorCode.UPLOAD_OFFSET_MISMATCH:
        expected_offset = int(error["details"]["expected_offset"])
        raise _OffsetCorrectionError(expected_offset)
    if response.status_code == 422 and error.get("code") == ErrorCode.HASH_MISMATCH:
        raise HashMismatchError(file_sha256, None)
    if response.status_code >= 500:
        raise _RetryableHttpError(f"server answered HTTP {response.status_code}")
    raise UploadFailedError(f"upload answered HTTP {response.status_code}: {response.text}")


def _error_body(response: httpx.Response) -> dict:
    """The "error" object of our standard error shape, or {} if absent."""
    try:
        return response.json().get("error", {})
    except ValueError:
        return {}
