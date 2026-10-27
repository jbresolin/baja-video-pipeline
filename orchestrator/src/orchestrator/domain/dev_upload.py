"""POST /intake/test-upload: dev-only synchronous intake of a multipart file."""

import hashlib
from typing import BinaryIO

from orchestrator.context import OrchestratorContext
from orchestrator.domain.intake import create_received_job
from orchestrator.errors import ApiError
from orchestrator.security import new_id
from orchestrator.storage import raw_extension_for
from shared.http_transfer import partial_path_for
from shared.models import ErrorCode, IntakeResponse, ReceivedAs


def ingest_test_upload(
    context: OrchestratorContext,
    file: BinaryIO,
    original_filename: str | None,
    mimetype: str | None,
    sender_name: str | None,
    received_as: ReceivedAs,
) -> IntakeResponse:
    """Dev-only synchronous intake of a multipart upload (POST /intake/test-upload)."""
    if context.layout.inbox_bytes() > context.settings.inbox_cap_bytes:
        raise ApiError(413, ErrorCode.INBOX_FULL, "the VPS inbox is full")
    if received_as == ReceivedAs.DOCUMENT:
        raise ApiError(400, ErrorCode.VALIDATION_ERROR, "received_as must be test_upload or video")

    job_id = new_id()
    raw_extension = raw_extension_for(original_filename, mimetype)
    destination = context.layout.inbox_path(job_id, raw_extension)
    partial = partial_path_for(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    with partial.open("wb") as output:
        while chunk := file.read(1024 * 1024):
            digest.update(chunk)
            output.write(chunk)
    size_bytes = partial.stat().st_size
    partial.replace(destination)

    message_id = f"test-{job_id}"
    with context.database.transaction() as connection:
        create_received_job(
            connection,
            context.clock.now(),
            job_id=job_id,
            message_id=message_id,
            sender_jid=None,
            sender_name=sender_name,
            received_as=received_as,
            original_filename=original_filename,
            mimetype=mimetype,
            sha256=digest.hexdigest(),
            size_bytes=size_bytes,
            raw_extension=raw_extension,
            actor="admin",
        )
    return IntakeResponse(
        job_id=job_id,
        accepted=True,
        low_res=received_as == ReceivedAs.VIDEO,
    )
