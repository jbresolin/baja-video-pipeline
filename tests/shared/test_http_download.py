"""Tests for shared.http_transfer.download_file (resumable downloads).

Most tests use the in-process fake server from conftest.py. A download is
"resumable" when, after being cut off, it continues from where it stopped
instead of starting again. We simulate "cut off" in two ways:
  * by leaving a half-written ".part" file on disk before downloading;
  * by a fake transport that breaks the connection in the middle.
"""

import hashlib

import httpx
import pytest

from shared.http_transfer import (
    DownloadFailedError,
    HashMismatchError,
    download_file,
    partial_path_for,
)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def test_fresh_download_writes_the_file_and_verifies_the_hash(
    fake_server_factory, sample_bytes, tmp_path
):
    # Setup: a server holding sample_bytes, and nothing on our disk yet.
    server = fake_server_factory(sample_bytes)
    destination = tmp_path / "raw.mp4"

    result = download_file(server.client, "/file", destination, sha(sample_bytes))

    # The final file has exactly the server's bytes...
    assert destination.read_bytes() == sample_bytes
    # ...the temporary ".part" file is gone (it was renamed)...
    assert not partial_path_for(destination).exists()
    # ...and the result reports a fresh start (nothing resumed) in one go.
    assert result.resumed_from_byte == 0
    assert result.attempts == 1
    assert result.sha256 == sha(sample_bytes)
    # A fresh download must NOT ask for a byte range.
    assert "range" not in server.download_request_headers[0]


def test_existing_partial_file_is_resumed_with_a_range_request(
    fake_server_factory, sample_bytes, tmp_path
):
    # Setup: pretend an earlier attempt saved the first 100,000 bytes.
    server = fake_server_factory(sample_bytes)
    destination = tmp_path / "raw.mp4"
    partial_path_for(destination).write_bytes(sample_bytes[:100_000])

    result = download_file(server.client, "/file", destination, sha(sample_bytes))

    # The client must have asked only for the missing bytes, starting at
    # byte 100,000 — that is what "resume" means.
    assert server.download_request_headers[0]["range"] == "bytes=100000-"
    # Old part + new part together must be the full, correct file.
    assert destination.read_bytes() == sample_bytes
    assert result.resumed_from_byte == 100_000


def test_server_that_ignores_range_makes_the_client_start_over(
    fake_server_factory, sample_bytes, tmp_path
):
    # Setup: a partial file exists, but this server always answers 200
    # with the whole file. Appending would duplicate the first bytes, so
    # the client must overwrite the partial file instead.
    server = fake_server_factory(sample_bytes, honour_range=False)
    destination = tmp_path / "raw.mp4"
    partial_path_for(destination).write_bytes(sample_bytes[:100_000])

    download_file(server.client, "/file", destination, sha(sample_bytes))

    # If the client had appended, the file would be 100,000 bytes too long.
    assert destination.read_bytes() == sample_bytes


def test_partial_file_that_is_already_complete_needs_no_more_bytes(
    fake_server_factory, sample_bytes, tmp_path
):
    # Setup: the previous attempt got every byte but crashed before the
    # rename. Asking for "bytes from the end" gets 416 from the server.
    server = fake_server_factory(sample_bytes)
    destination = tmp_path / "raw.mp4"
    partial_path_for(destination).write_bytes(sample_bytes)

    download_file(server.client, "/file", destination, sha(sample_bytes))

    # The client treats 416 as "already complete", checks the hash, renames.
    assert destination.read_bytes() == sample_bytes


def test_wrong_hash_raises_and_deletes_the_partial_file(
    fake_server_factory, sample_bytes, tmp_path
):
    # Setup: we expect the hash of *different* content, as if the server
    # sent a corrupted or wrong file.
    server = fake_server_factory(sample_bytes)
    destination = tmp_path / "raw.mp4"

    with pytest.raises(HashMismatchError):
        download_file(server.client, "/file", destination, sha(b"something else"))

    # Neither the final file nor the partial may remain: a bad partial
    # would otherwise be "resumed" and fail again forever.
    assert not destination.exists()
    assert not partial_path_for(destination).exists()


def test_without_expected_hash_the_computed_hash_is_still_returned(
    fake_server_factory, sample_bytes, tmp_path
):
    # The orchestrator downloads from Evolution without knowing the hash in
    # advance; it needs the computed hash back to create the job.
    server = fake_server_factory(sample_bytes)
    result = download_file(server.client, "/file", tmp_path / "raw.mp4")
    assert result.sha256 == sha(sample_bytes)


class _BrokenAfterSomeBytes(httpx.SyncByteStream):
    """A response body that sends some bytes and then "loses the connection"."""

    def __init__(self, data: bytes) -> None:
        self.data = data

    def __iter__(self):
        yield self.data
        raise httpx.ReadError("connection reset by peer (simulated)")


def test_connection_drop_mid_download_is_retried_from_where_it_stopped(sample_bytes, tmp_path):
    # Setup: a fake transport (httpx.MockTransport calls our function
    # instead of using the network). The FIRST request gets 120,000 bytes
    # and then a simulated connection drop. Later requests behave normally.
    seen_ranges = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_ranges.append(request.headers.get("range"))
        if len(seen_ranges) == 1:
            return httpx.Response(200, stream=_BrokenAfterSomeBytes(sample_bytes[:120_000]))
        start = int(request.headers["range"].removeprefix("bytes=").removesuffix("-"))
        total = len(sample_bytes)
        headers = {"Content-Range": f"bytes {start}-{total - 1}/{total}"}
        return httpx.Response(206, content=sample_bytes[start:], headers=headers)

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://vps")
    destination = tmp_path / "raw.mp4"
    # sleep=lambda: ... replaces the real pause between retries with a
    # do-nothing function, so the test runs instantly.
    result = download_file(
        client, "/file", destination, sha(sample_bytes), sleep=lambda seconds: None
    )

    # First request: no range. Second request: continue at byte 120,000.
    assert seen_ranges == [None, "bytes=120000-"]
    assert destination.read_bytes() == sample_bytes
    assert result.attempts == 2


def test_gives_up_after_max_attempts(tmp_path):
    # Setup: a transport that fails every single time.
    def always_fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host (simulated)")

    client = httpx.Client(transport=httpx.MockTransport(always_fail), base_url="http://vps")

    # After 3 failed attempts the helper must stop and raise, not loop forever.
    with pytest.raises(DownloadFailedError):
        download_file(client, "/file", tmp_path / "raw.mp4", max_attempts=3, sleep=lambda s: None)


def test_not_found_fails_immediately_without_retrying(tmp_path):
    # A 404 will not fix itself, so retrying would only waste time.
    calls = []

    def not_found(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(not_found), base_url="http://vps")
    with pytest.raises(DownloadFailedError):
        download_file(client, "/file", tmp_path / "raw.mp4", sleep=lambda s: None)
    assert len(calls) == 1


def test_range_answer_starting_at_the_wrong_byte_is_not_appended(sample_bytes, tmp_path):
    # Setup: the first answer claims to start at byte 0 although we asked
    # for byte 50,000. Appending it would corrupt the file, so the client
    # must discard the partial and retry; the retry (no partial left now)
    # gets a normal full 200 answer.
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.headers.get("range"))
        if len(calls) == 1:
            total = len(sample_bytes)
            headers = {"Content-Range": f"bytes 0-{total - 1}/{total}"}
            return httpx.Response(206, content=sample_bytes, headers=headers)
        return httpx.Response(200, content=sample_bytes)

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://vps")
    destination = tmp_path / "raw.mp4"
    partial_path_for(destination).write_bytes(sample_bytes[:50_000])

    download_file(client, "/file", destination, sha(sample_bytes), sleep=lambda s: None)

    assert calls == ["bytes=50000-", None]
    assert destination.read_bytes() == sample_bytes


def test_partial_download_is_a_hidden_dot_file(tmp_path):
    # Design §15: writers use dot-files until a file is complete, and Caddy
    # never serves dot-files. So a half-downloaded inbox file is invisible.
    partial = partial_path_for(tmp_path / "inbox" / "job1.mp4")
    assert partial.name == ".job1.mp4.part"
    assert partial.parent == tmp_path / "inbox"
