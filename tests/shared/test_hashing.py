"""Tests for shared.hashing: SHA-256 of files read in chunks.

We compare our chunked result with Python's own one-shot hashlib result.
If chunking dropped or repeated even one byte, the two would differ.
"""

import hashlib
import os

import pytest

from shared.hashing import sha256_of_bytes, sha256_of_file


@pytest.fixture
def generated_file(tmp_path):
    """Write a ~2.5 MB file of random bytes and return (path, its bytes).

    2.5 MB is more than two 1 MB chunks plus a partial chunk, so the
    "last chunk is shorter" case is exercised too.
    """
    content = os.urandom(2_500_000)
    path = tmp_path / "generated.bin"
    path.write_bytes(content)
    return path, content


def test_hash_of_file_matches_hashlib_on_the_whole_content(generated_file):
    path, content = generated_file
    # hashlib.sha256(content) hashes everything at once in memory: our
    # reference answer. sha256_of_file must reach the same answer by
    # reading the file piece by piece.
    expected = hashlib.sha256(content).hexdigest()
    assert sha256_of_file(path) == expected


@pytest.mark.parametrize("chunk_size", [1, 1000, 1024 * 1024, 10 * 1024 * 1024])
def test_chunk_size_does_not_change_the_result(generated_file, chunk_size):
    # Tiny chunks (1 byte), odd sizes, the default, and a chunk bigger
    # than the file must all give the same hash. chunk_size=1 is slow,
    # so for that case we only hash the first 5000 bytes of the content.
    path, content = generated_file
    if chunk_size == 1:
        path.write_bytes(content[:5000])
        content = content[:5000]
    assert sha256_of_file(path, chunk_size=chunk_size) == hashlib.sha256(content).hexdigest()


def test_hash_is_64_lowercase_hex_characters(generated_file):
    # The API stores and compares hashes as text, so the exact format
    # matters: "ABC" and "abc" would not be equal strings.
    digest = sha256_of_file(generated_file[0])
    assert len(digest) == 64
    assert digest == digest.lower()
    int(digest, 16)  # would raise ValueError if any character were not hex


def test_empty_file_has_the_well_known_empty_hash(tmp_path):
    # The SHA-256 of zero bytes is a fixed, published value. An empty
    # file must give exactly that (and not crash).
    empty = tmp_path / "empty.bin"
    empty.write_bytes(b"")
    assert sha256_of_file(empty) == (
        "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    )


def test_bytes_and_file_versions_agree(tmp_path):
    # sha256_of_bytes is the in-memory twin of sha256_of_file; for the same
    # content they must return the same text.
    path = tmp_path / "small.bin"
    path.write_bytes(b"baja")
    assert sha256_of_bytes(b"baja") == sha256_of_file(path)


def test_zero_chunk_size_is_rejected(generated_file):
    # A chunk size of 0 would loop forever reading nothing; refuse it.
    with pytest.raises(ValueError):
        sha256_of_file(generated_file[0], chunk_size=0)
