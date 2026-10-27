"""SHA-256 of files without loading them into memory.

Raws can be 2 GB, so the file is read in fixed-size chunks and each chunk is
fed to the hash in turn. Memory use stays at one chunk whatever the file size.
"""

import hashlib
from pathlib import Path

DEFAULT_HASH_CHUNK_BYTES = 1024 * 1024


def sha256_of_file(path: Path, chunk_size: int = DEFAULT_HASH_CHUNK_BYTES) -> str:
    """Return the SHA-256 of the file as 64 lowercase hex characters."""
    if chunk_size <= 0:
        raise ValueError("chunk_size must be a positive number of bytes")
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while chunk := file.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_of_bytes(data: bytes) -> str:
    """Same format as sha256_of_file, for data already in memory (tests, small files)."""
    return hashlib.sha256(data).hexdigest()
