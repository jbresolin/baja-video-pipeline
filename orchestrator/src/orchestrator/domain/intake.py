"""Intake: Evolution webhook → background download + decrypt → inbox → job (docs/api.md §7.1).

The webhook answers within milliseconds (Evolution retries slow or failed
deliveries). The actual work happens in run_media_download(), started in
the background, and the job only exists once the media is downloaded,
decrypted and every hash checked. Bot replies are sent outside any DB
transaction, so a slow Evolution never holds the DB lock.
"""

import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from pydantic import ValidationError

from orchestrator.context import OrchestratorContext
from orchestrator.db.database import to_db_time
from orchestrator.domain import bot_messages
from orchestrator.errors import ApiError
from orchestrator.repositories import events as events_repo
from orchestrator.repositories import jobs as jobs_repo
from orchestrator.security import new_id
from orchestrator.storage import delete_file_and_partials, raw_extension_for
from shared.models import (
    ErrorCode,
    EvolutionWebhook,
    IncomingMedia,
    IntakeResponse,
    JobState,
    ReceivedAs,
    find_incoming_video,
)
from shared.whatsapp_media import download_incoming_media

logger = logging.getLogger(__name__)

UPSERT_EVENT = "messages.upsert"
EVOLUTION_ACTOR = "evolution"

# intake_messages.status values
DOWNLOADING = "downloading"
DONE = "done"
FAILED = "failed"
REJECTED_INBOX_FULL = "rejected_inbox_full"


@dataclass(frozen=True)
class PendingMediaDownload:
    """Everything the background download needs; the job id is chosen up front."""

    job_id: str
    message_id: str
    incoming: IncomingMedia
    sender_jid: str
    sender_name: str | None
    raw_extension: str


@dataclass
class WebhookOutcome:
    status_code: int
    response: IntakeResponse | None = None
    error: ApiError | None = None
    download: PendingMediaDownload | None = None
    # (recipient jid, text) pairs to send after the DB work is done.
    replies: list[tuple[str, str]] = field(default_factory=list)


def ignored(reason: str, message_id: str | None = None) -> WebhookOutcome:
    response = IntakeResponse(job_id=None, message_id=message_id, ignored=True, reason=reason)
    return WebhookOutcome(status_code=200, response=response)


def handle_webhook(context: OrchestratorContext, payload: Any) -> WebhookOutcome:
    """Decide what to do with one webhook body. Does not download anything."""
    # Other events (connection.update, ...) are acknowledged without
    # validation: a 400 would make Evolution retry them for 20 minutes.
    if not isinstance(payload, dict) or payload.get("event") != UPSERT_EVENT:
        return ignored("not a messages.upsert event")
    try:
        webhook = EvolutionWebhook.model_validate(payload)
    except ValidationError as error:
        raise ApiError(
            400,
            ErrorCode.VALIDATION_ERROR,
            "webhook body is not a messages.upsert event",
            {"errors": error.errors(include_url=False, include_context=False)},
        ) from error

    key = webhook.data.key
    if key.from_me:
        return ignored("sent by the bot itself", key.id)
    incoming = find_incoming_video(webhook.data.message)
    if incoming is None:
        return ignored("not a video document or video message", key.id)

    with context.database.transaction() as connection:
        return _accept_incoming_video(context, connection, webhook, incoming)


def _accept_incoming_video(
    context: OrchestratorContext,
    connection: sqlite3.Connection,
    webhook: EvolutionWebhook,
    incoming: IncomingMedia,
) -> WebhookOutcome:
    now = context.clock.now()
    key = webhook.data.key
    low_res = incoming.received_as == ReceivedAs.VIDEO

    known = jobs_repo.get_intake_message(connection, key.id)
    if known is not None and known["status"] in (DOWNLOADING, DONE):
        response = IntakeResponse(
            job_id=known["job_id"] if known["status"] == DONE else None,
            message_id=key.id,
            duplicate=True,
            reason="duplicate message",
            low_res=low_res,
        )
        return WebhookOutcome(status_code=200, response=response)

    inbox_bytes = context.layout.inbox_bytes()
    incoming_bytes = incoming.media.file_length or 0
    if inbox_bytes + incoming_bytes > context.settings.inbox_cap_bytes:
        # Evolution retries a 413 for ~20 minutes; reply to the sender only once.
        replies = []
        if known is None or known["status"] != REJECTED_INBOX_FULL:
            replies.append((key.remote_jid, bot_messages.INBOX_FULL_RESEND_LATER))
        jobs_repo.save_intake_message(
            connection, key.id, REJECTED_INBOX_FULL, now, sender_jid=key.remote_jid
        )
        error = ApiError(
            413,
            ErrorCode.INBOX_FULL,
            "the VPS inbox is full; try again later",
            {"inbox_bytes": inbox_bytes, "inbox_cap_bytes": context.settings.inbox_cap_bytes},
        )
        return WebhookOutcome(status_code=413, error=error, replies=replies)

    replies = []
    if low_res:
        replies.append((key.remote_jid, bot_messages.LOW_RES_RESEND_AS_DOCUMENT))
    if inbox_bytes + incoming_bytes > context.settings.inbox_hold_bytes:
        replies.append((key.remote_jid, bot_messages.INBOX_NEARLY_FULL_PLEASE_HOLD))

    jobs_repo.save_intake_message(connection, key.id, DOWNLOADING, now, sender_jid=key.remote_jid)
    download = PendingMediaDownload(
        job_id=new_id(),
        message_id=key.id,
        incoming=incoming,
        sender_jid=key.remote_jid,
        sender_name=webhook.data.push_name,
        raw_extension=raw_extension_for(incoming.media.file_name, incoming.media.mimetype),
    )
    response = IntakeResponse(job_id=None, message_id=key.id, accepted=True, low_res=low_res)
    return WebhookOutcome(status_code=202, response=response, download=download, replies=replies)


def send_replies(context: OrchestratorContext, replies: list[tuple[str, str]]) -> None:
    for recipient, text in replies:
        context.evolution.send_text(recipient, text)


def run_media_download(context: OrchestratorContext, pending: PendingMediaDownload) -> str | None:
    """Download, decrypt and verify the media, then create the job. Returns the job id or None.

    Runs in the background after the webhook answered. Any failure leaves
    no file and no job behind, and the bot asks the sender to resend.
    """
    destination = context.layout.inbox_path(pending.job_id, pending.raw_extension)
    try:
        media = download_incoming_media(
            context.media_http_client,
            pending.incoming,
            destination,
            context.settings.whatsapp_media_base_url,
        )
    except Exception as error:  # noqa: BLE001 - any failure means "ask for a resend"
        logger.warning("intake of message %s failed: %s", pending.message_id, error)
        delete_file_and_partials(destination)
        with context.database.transaction() as connection:
            jobs_repo.save_intake_message(
                connection, pending.message_id, FAILED, context.clock.now(), detail=str(error)
            )
        context.evolution.send_text(pending.sender_jid, bot_messages.DOWNLOAD_FAILED_PLEASE_RESEND)
        return None

    with context.database.transaction() as connection:
        create_received_job(
            connection,
            context.clock.now(),
            job_id=pending.job_id,
            message_id=pending.message_id,
            sender_jid=pending.sender_jid,
            sender_name=pending.sender_name,
            received_as=pending.incoming.received_as,
            original_filename=pending.incoming.media.file_name,
            mimetype=pending.incoming.media.mimetype,
            sha256=media.sha256,
            size_bytes=media.size_bytes,
            raw_extension=pending.raw_extension,
            actor=EVOLUTION_ACTOR,
        )
    return pending.job_id


def create_received_job(
    connection: sqlite3.Connection,
    now: datetime,
    *,
    job_id: str,
    message_id: str,
    sender_jid: str | None,
    sender_name: str | None,
    received_as: ReceivedAs,
    original_filename: str | None,
    mimetype: str | None,
    sha256: str,
    size_bytes: int,
    raw_extension: str,
    actor: str,
) -> None:
    """Insert a job in state `received` (its raw is already in the inbox)."""
    jobs_repo.insert_job(
        connection,
        {
            "id": job_id,
            "state": str(JobState.RECEIVED),
            "created_at": to_db_time(now),
            "updated_at": to_db_time(now),
            "message_id": message_id,
            "sender_jid": sender_jid,
            "sender_name": sender_name,
            "received_as": str(received_as),
            "original_filename": original_filename,
            "mimetype": mimetype,
            "low_res": int(received_as == ReceivedAs.VIDEO),
            "sha256": sha256,
            "size_bytes": size_bytes,
            "raw_extension": raw_extension,
        },
    )
    jobs_repo.save_intake_message(connection, message_id, DONE, now, job_id=job_id)
    events_repo.record_event(
        connection,
        now,
        actor=actor,
        kind="job_created",
        job_id=job_id,
        to_state=str(JobState.RECEIVED),
        reason=f"{received_as} from {sender_name or sender_jid or 'unknown sender'}",
    )
