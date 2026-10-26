"""Server side of the chunked upload protocol (docs/api.md, "Chunked uploads").

Used by the orchestrator (proxy and clip uploads) and by the studio receiver
(direct clip pushes), so both behave exactly the same way. It only deals
with files; turning its errors into HTTP answers is the web layer's job:

    UploadOffsetMismatchError → 409 upload_offset_mismatch {"expected_offset": n}
    UploadHashMismatchError   → 422 hash_mismatch
    UploadTooLargeError       → 413 upload_too_large
    InvalidUploadHeadersError → 400 validation_error

Not thread-safe: the caller must not feed two chunks for the same file at
the same time (one upload per job at a time is the rule anyway).
"""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from shared.hashing import sha256_of_file
from shared.models.api_worker_loop import ChunkAccepted
from shared.protocol import UPLOAD_LENGTH_HEADER, UPLOAD_OFFSET_HEADER, UPLOAD_SHA256_HEADER


class UploadOffsetMismatchError(Exception):
    def __init__(self, expected_offset: int) -> None:
        self.expected_offset = expected_offset
        super().__init__(f"expected a chunk starting at byte {expected_offset}")


class UploadHashMismatchError(Exception):
    pass


class UploadTooLargeError(Exception):
    pass


class InvalidUploadHeadersError(ValueError):
    pass


@dataclass(frozen=True)
class UploadHeaders:
    offset: int
    total_length: int
    sha256: str


def read_upload_headers(headers: Mapping[str, str]) -> UploadHeaders:
    """Parse and check the three Upload-* headers of one chunk request.

    `headers` should be case-insensitive (Starlette and httpx headers are).
    """
    try:
        offset = int(headers[UPLOAD_OFFSET_HEADER])
        total_length = int(headers[UPLOAD_LENGTH_HEADER])
        sha256 = headers[UPLOAD_SHA256_HEADER].strip().lower()
    except KeyError as missing:
        raise InvalidUploadHeadersError(f"missing header {missing}") from missing
    except ValueError as not_a_number:
        raise InvalidUploadHeadersError("Upload-Offset and Upload-Length must be integers") from (
            not_a_number
        )
    if offset < 0 or total_length < 0:
        raise InvalidUploadHeadersError("Upload-Offset and Upload-Length must not be negative")
    if len(sha256) != 64 or any(char not in "0123456789abcdef" for char in sha256):
        raise InvalidUploadHeadersError("Upload-SHA256 must be 64 hex characters")
    return UploadHeaders(offset, total_length, sha256)


class ChunkedUploadReceiver:
    """Assembles one file from chunks, next to its final location.

    While the upload is in progress the bytes live in "<final>.part" and a
    small "<final>.part.json" remembers which file (length + SHA-256) they
    belong to. If a chunk for a *different* file arrives, the old partial
    data is thrown away and the new upload must start at offset 0.
    """

    def __init__(self, final_path: Path, max_bytes: int | None = None) -> None:
        self.final_path = final_path
        self.partial_path = final_path.with_name(final_path.name + ".part")
        self.metadata_path = final_path.with_name(final_path.name + ".part.json")
        self.max_bytes = max_bytes

    def accept_chunk(self, upload: UploadHeaders, chunk: bytes) -> ChunkAccepted:
        if self.max_bytes is not None and upload.total_length > self.max_bytes:
            raise UploadTooLargeError(f"{upload.total_length} bytes is over {self.max_bytes}")

        if not self._partial_belongs_to(upload):
            if self._final_file_already_matches(upload):
                # The client is re-sending a file we already finished, e.g.
                # because it never saw our last "complete" answer.
                return ChunkAccepted(received_bytes=upload.total_length, complete=True)
            self._start_fresh(upload)

        received_so_far = self.partial_path.stat().st_size
        if upload.offset != received_so_far:
            raise UploadOffsetMismatchError(received_so_far)
        if upload.offset + len(chunk) > upload.total_length:
            raise UploadTooLargeError("chunk goes past Upload-Length")

        with self.partial_path.open("ab") as partial_file:
            partial_file.write(chunk)
        received_so_far += len(chunk)

        if received_so_far < upload.total_length:
            return ChunkAccepted(received_bytes=received_so_far, complete=False)
        return self._finish(upload)

    def _finish(self, upload: UploadHeaders) -> ChunkAccepted:
        actual_sha256 = sha256_of_file(self.partial_path)
        if actual_sha256 != upload.sha256:
            self._discard_partial()
            raise UploadHashMismatchError(f"expected {upload.sha256}, got {actual_sha256}")
        # replace() overwrites an older final file, e.g. a reprocessed clip.
        self.partial_path.replace(self.final_path)
        self.metadata_path.unlink(missing_ok=True)
        return ChunkAccepted(received_bytes=upload.total_length, complete=True)

    def _partial_belongs_to(self, upload: UploadHeaders) -> bool:
        if not (self.partial_path.exists() and self.metadata_path.exists()):
            return False
        metadata = json.loads(self.metadata_path.read_text())
        return metadata == {"total_length": upload.total_length, "sha256": upload.sha256}

    def _final_file_already_matches(self, upload: UploadHeaders) -> bool:
        return (
            self.final_path.exists()
            and self.final_path.stat().st_size == upload.total_length
            and sha256_of_file(self.final_path) == upload.sha256
        )

    def _start_fresh(self, upload: UploadHeaders) -> None:
        self.final_path.parent.mkdir(parents=True, exist_ok=True)
        self.partial_path.write_bytes(b"")
        metadata = {"total_length": upload.total_length, "sha256": upload.sha256}
        self.metadata_path.write_text(json.dumps(metadata))

    def _discard_partial(self) -> None:
        self.partial_path.unlink(missing_ok=True)
        self.metadata_path.unlink(missing_ok=True)
