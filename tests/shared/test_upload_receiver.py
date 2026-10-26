"""Tests for shared.upload_receiver (the server side of chunked uploads).

These call the receiver directly, without any web server, to check its
rules one by one: chunks must arrive in order, the file is only "complete"
when the hash matches, and a different file resets the upload.
"""

import hashlib

import pytest

from shared.upload_receiver import (
    ChunkedUploadReceiver,
    InvalidUploadHeadersError,
    UploadHashMismatchError,
    UploadHeaders,
    UploadOffsetMismatchError,
    UploadTooLargeError,
    read_upload_headers,
)

CONTENT = b"0123456789" * 10  # 100 bytes, easy to slice by hand
CONTENT_SHA = hashlib.sha256(CONTENT).hexdigest()


def headers_at(offset: int, sha256: str = CONTENT_SHA, total: int = len(CONTENT)) -> UploadHeaders:
    return UploadHeaders(offset=offset, total_length=total, sha256=sha256)


@pytest.fixture
def receiver(tmp_path):
    return ChunkedUploadReceiver(tmp_path / "out" / "clip.mp4")


def test_chunks_in_order_build_the_final_file(receiver):
    first = receiver.accept_chunk(headers_at(0), CONTENT[:40])
    # After the first 40 bytes: not complete, next expected offset is 40.
    assert (first.received_bytes, first.complete) == (40, False)
    # The final file must not exist yet — only the ".part" file.
    assert not receiver.final_path.exists()

    last = receiver.accept_chunk(headers_at(40), CONTENT[40:])
    assert (last.received_bytes, last.complete) == (100, True)
    assert receiver.final_path.read_bytes() == CONTENT
    # Temporary files are cleaned up once the file is complete.
    assert not receiver.partial_path.exists()
    assert not receiver.metadata_path.exists()


def test_chunk_at_the_wrong_offset_is_refused_with_the_expected_offset(receiver):
    receiver.accept_chunk(headers_at(0), CONTENT[:40])
    # Skipping ahead to 60 would leave a hole from 40 to 60.
    with pytest.raises(UploadOffsetMismatchError) as caught:
        receiver.accept_chunk(headers_at(60), CONTENT[60:])
    # The error tells the client where to continue: byte 40.
    assert caught.value.expected_offset == 40


def test_wrong_hash_discards_everything(receiver):
    wrong = "f" * 64
    with pytest.raises(UploadHashMismatchError):
        receiver.accept_chunk(headers_at(0, sha256=wrong), CONTENT)
    # Nothing is kept: neither a final file nor a partial one.
    assert not receiver.final_path.exists()
    assert not receiver.partial_path.exists()


def test_a_different_file_resets_the_partial_upload(receiver):
    # Start uploading CONTENT, then a *different* file arrives (new hash),
    # e.g. a re-rendered clip. The old bytes belong to another file and
    # must be thrown away; the new upload must start from 0.
    receiver.accept_chunk(headers_at(0), CONTENT[:40])
    other = b"x" * 100
    other_sha = hashlib.sha256(other).hexdigest()
    with pytest.raises(UploadOffsetMismatchError) as caught:
        receiver.accept_chunk(headers_at(40, sha256=other_sha), other[40:])
    assert caught.value.expected_offset == 0


def test_resending_a_finished_file_is_answered_complete(receiver):
    # The client sent the last chunk but never saw our "complete" answer
    # (network drop), so it starts again. We already have the identical
    # file, so we say "complete" without making it upload anything.
    receiver.accept_chunk(headers_at(0), CONTENT)
    again = receiver.accept_chunk(headers_at(0), CONTENT[:10])
    assert again.complete is True
    assert receiver.final_path.read_bytes() == CONTENT


def test_new_version_replaces_an_older_final_file(receiver):
    # Reprocessing renders a new clip for the same job: the new upload
    # must overwrite the old file once it is complete and verified.
    receiver.accept_chunk(headers_at(0), CONTENT)
    newer = b"n" * 50
    newer_headers = UploadHeaders(0, len(newer), hashlib.sha256(newer).hexdigest())
    assert receiver.accept_chunk(newer_headers, newer).complete
    assert receiver.final_path.read_bytes() == newer


def test_chunk_past_the_declared_length_is_refused(receiver):
    with pytest.raises(UploadTooLargeError):
        receiver.accept_chunk(headers_at(0, total=10), CONTENT[:20])


def test_file_over_the_size_limit_is_refused(tmp_path):
    small_receiver = ChunkedUploadReceiver(tmp_path / "clip.mp4", max_bytes=50)
    with pytest.raises(UploadTooLargeError):
        small_receiver.accept_chunk(headers_at(0), CONTENT[:10])


def test_read_upload_headers_parses_valid_headers():
    parsed = read_upload_headers(
        {"Upload-Offset": "40", "Upload-Length": "100", "Upload-SHA256": CONTENT_SHA.upper()}
    )
    # Hex is normalised to lowercase so comparisons with stored hashes work.
    assert parsed == UploadHeaders(40, 100, CONTENT_SHA)


@pytest.mark.parametrize(
    "bad_headers",
    [
        {},  # nothing at all
        {"Upload-Offset": "x", "Upload-Length": "100", "Upload-SHA256": CONTENT_SHA},
        {"Upload-Offset": "-1", "Upload-Length": "100", "Upload-SHA256": CONTENT_SHA},
        {"Upload-Offset": "0", "Upload-Length": "100", "Upload-SHA256": "not-a-hash"},
    ],
)
def test_read_upload_headers_rejects_bad_headers(bad_headers):
    # Each case is one way a client could get the headers wrong; all of
    # them must become a clear InvalidUploadHeadersError (→ HTTP 400).
    with pytest.raises(InvalidUploadHeadersError):
        read_upload_headers(bad_headers)
