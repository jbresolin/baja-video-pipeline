"""Pieces of the fake orchestrator: the job record, errors, and byte handling.

Split out of fake_orchestrator.py only to keep each file short.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from starlette.responses import JSONResponse, Response

from shared.hashing import sha256_of_bytes
from shared.models import (
    ChunkAccepted,
    ClipInfo,
    DeliveryRoute,
    Holder,
    IntakeSource,
    Job,
    JobState,
    ReceivedAs,
)
from shared.upload_receiver import (
    ChunkedUploadReceiver,
    UploadHashMismatchError,
    UploadOffsetMismatchError,
    read_upload_headers,
)


def error(status: int, code: str, message: str = "", **details) -> JSONResponse:
    return JSONResponse(
        {"error": {"code": code, "message": message or code, "details": details}},
        status_code=status,
    )


@dataclass
class FakeJob:
    job_id: str
    raw_bytes: bytes
    original_filename: str = "clip.mp4"
    prova: str | None = None
    car_number: int | None = None
    team_name: str | None = None
    passagem: int | None = None
    holders: list[Holder] = field(default_factory=list)
    # What GET /jobs/{id} says about the job's state and its current clip
    # (a test sets "delivered" + the clip's hash to play a late studio ACK).
    state: JobState = JobState.READY
    clip_sha256: str | None = None

    @property
    def sha256(self) -> str:
        return sha256_of_bytes(self.raw_bytes)

    def as_job(self) -> Job:
        return Job(
            id=self.job_id,
            state=self.state,
            source=IntakeSource(
                message_id=f"msg-{self.job_id}",
                received_as=ReceivedAs.DOCUMENT,
                original_filename=self.original_filename,
            ),
            sha256=self.sha256,
            size_bytes=len(self.raw_bytes),
            prova=self.prova,
            car_number=self.car_number,
            team_name=self.team_name,
            passagem=self.passagem,
            holders=self.holders,
            clip=None
            if self.clip_sha256 is None
            else ClipInfo(
                sha256=self.clip_sha256,
                size_bytes=1,
                relative_path="enduro/42/enduro-042-p3.mp4",
                route=DeliveryRoute.DIRECT,
                rendered_by_worker_id="worker-1",
            ),
        )


def range_response(content: bytes, range_header: str | None) -> Response:
    """Serve `content` like Caddy: 200 whole, 206 from "bytes=<start>-", or 416."""
    if range_header is None:
        return Response(content, status_code=200)
    start = int(range_header.removeprefix("bytes=").removesuffix("-"))
    if start >= len(content):
        return Response(status_code=416)
    content_range = f"bytes {start}-{len(content) - 1}/{len(content)}"
    return Response(content[start:], status_code=206, headers={"Content-Range": content_range})


def receive_chunk(
    destination: Path, headers: Mapping[str, str], chunk: bytes
) -> ChunkAccepted | Response:
    """Feed one chunk to the real ChunkedUploadReceiver; an error Response if refused."""
    receiver = ChunkedUploadReceiver(destination)
    try:
        return receiver.accept_chunk(read_upload_headers(headers), chunk)
    except UploadOffsetMismatchError as mismatch:
        return error(409, "upload_offset_mismatch", expected_offset=mismatch.expected_offset)
    except UploadHashMismatchError:
        return error(422, "hash_mismatch")
