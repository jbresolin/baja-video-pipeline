"""Tests for shared.http_transfer.upload_file_in_chunks (client side).

The client sends a file as several POSTs ("chunks"). The fake server in
conftest.py uses the real ChunkedUploadReceiver, so these tests also prove
that the client and the receiver speak the same protocol.
"""

import hashlib

import httpx
import pytest

from shared.http_transfer import HashMismatchError, UploadFailedError, upload_file_in_chunks
from shared.upload_receiver import ChunkedUploadReceiver, UploadHeaders


@pytest.fixture
def source_file(tmp_path, sample_bytes):
    """The file to upload: the ~300 KB of sample bytes from conftest.py."""
    path = tmp_path / "clip.mp4"
    path.write_bytes(sample_bytes)
    return path


def test_file_arrives_complete_in_several_chunks(fake_server_factory, source_file, sample_bytes):
    server = fake_server_factory(b"")
    # 100 KB chunks for a ~300 KB file → 3 requests (the last one shorter).
    result = upload_file_in_chunks(server.client, "/upload", source_file, chunk_size=100_000)

    # conftest.py makes the fake server store uploads at <tmp>/server/uploaded.bin.
    uploaded_path = source_file.parent / "server" / "uploaded.bin"
    # The server's final file is byte-for-byte the source.
    assert uploaded_path.read_bytes() == sample_bytes
    # Each request started where the previous one ended: 0, 100k, 200k.
    assert server.upload_log == [(200, 0), (200, 100_000), (200, 200_000)]
    assert result.requests_sent == 3
    assert result.sha256 == hashlib.sha256(sample_bytes).hexdigest()


def test_upload_resumes_at_the_offset_the_receiver_already_has(
    fake_server_factory, source_file, sample_bytes
):
    # Setup: the receiver already got the first 100,000 bytes earlier (for
    # example, the worker crashed mid-upload). We feed it directly.
    server = fake_server_factory(b"")
    uploaded_path = source_file.parent / "server" / "uploaded.bin"
    earlier = ChunkedUploadReceiver(uploaded_path)
    earlier.accept_chunk(
        UploadHeaders(0, len(sample_bytes), hashlib.sha256(sample_bytes).hexdigest()),
        sample_bytes[:100_000],
    )

    upload_file_in_chunks(server.client, "/upload", source_file, chunk_size=100_000)

    # The client first tries offset 0, gets "409, I expect 100000", then
    # continues from there instead of sending the first chunk again.
    assert server.upload_log == [(409, 0), (200, 100_000), (200, 200_000)]
    assert uploaded_path.read_bytes() == sample_bytes


def test_extra_headers_such_as_auth_and_lease_are_sent_on_every_chunk(source_file):
    # A mock transport records the headers of each request and pretends
    # to accept everything.
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers)
        end = int(request.headers["upload-offset"]) + len(request.content)
        total = int(request.headers["upload-length"])
        return httpx.Response(200, json={"received_bytes": end, "complete": end == total})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://vps")
    headers = {"Authorization": "Bearer abc", "Lease-Id": "lease-1"}
    upload_file_in_chunks(client, "/upload", source_file, headers=headers, chunk_size=150_000)

    # Two chunks, and both carried our auth and lease headers.
    assert len(seen) == 2
    for request_headers in seen:
        assert request_headers["authorization"] == "Bearer abc"
        assert request_headers["lease-id"] == "lease-1"
        assert request_headers["content-type"] == "application/octet-stream"


def test_wrong_hash_is_reported_as_hash_mismatch(fake_server_factory, source_file):
    # We lie about the file's hash. The receiver hashes what it got at the
    # end, sees the difference, and answers 422 hash_mismatch.
    server = fake_server_factory(b"")
    with pytest.raises(HashMismatchError):
        upload_file_in_chunks(server.client, "/upload", source_file, sha256="0" * 64)


def test_server_errors_are_retried_then_succeed(source_file, sample_bytes):
    # Setup: the first request gets a 503 (server busy), the rest succeed.
    attempts = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(int(request.headers["upload-offset"]))
        if len(attempts) == 1:
            return httpx.Response(503)
        end = int(request.headers["upload-offset"]) + len(request.content)
        return httpx.Response(
            200, json={"received_bytes": end, "complete": end == len(sample_bytes)}
        )

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://vps")
    upload_file_in_chunks(client, "/upload", source_file, sleep=lambda seconds: None)

    # Offset 0 was tried twice: once failed (503), once accepted.
    assert attempts == [0, 0]


def test_client_errors_fail_at_once(source_file):
    # 401 means our token is wrong; retrying cannot help.
    client = httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(401)), base_url="http://vps"
    )
    with pytest.raises(UploadFailedError):
        upload_file_in_chunks(client, "/upload", source_file, sleep=lambda seconds: None)


def test_empty_file_is_uploaded_with_a_single_empty_request(fake_server_factory, tmp_path):
    # Edge case: zero bytes. One request with an empty body must be enough
    # for the receiver to say "complete".
    empty = tmp_path / "empty.mp4"
    empty.write_bytes(b"")
    server = fake_server_factory(b"")
    result = upload_file_in_chunks(server.client, "/upload", empty)
    assert result.requests_sent == 1
    assert (tmp_path / "server" / "uploaded.bin").read_bytes() == b""
