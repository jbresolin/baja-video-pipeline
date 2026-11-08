"""Intake: Evolution webhook → background download + decrypt → inbox → job (docs/api.md §7.1).

Every private message first records its sender on the admin Contacts page
(domain/contacts.py), which also decides whether that sender's videos are
accepted (blocked senders and, in allowlist mode, senders not allowed are
not). A message that is not a video, from a sender whose videos would be
accepted, is kept for the job page (domain/sender_messages.py, LIVESTACK-49);
so are its edits and deletions (events messages.edited / messages.delete).
The webhook answers within milliseconds (Evolution retries slow or failed
deliveries). The actual work happens in run_media_download(), started in
the background, and the job only exists once the media is downloaded,
decrypted and every hash checked. A video already received in another
job that is not discarded (same SHA-256) still becomes a job, discarded at
once as a duplicate (domain/duplicates.py, LIVESTACK-51). Bot replies are
sent outside any DB transaction, so a slow Evolution never holds the DB lock.
"""

import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from pydantic import ValidationError

from orchestrator.context import OrchestratorContext
from orchestrator.db.database import to_db_time
from orchestrator.domain import bot_messages, contacts, duplicates, intake_notes
from orchestrator.domain.bot_replies import (
    REACTION_FAILED,
    REACTION_STORED,
    REACTION_WARNING,
    BotReply,
    VideoMessageRef,
    quoted_message_for,
    send_bot_reply,
)
from orchestrator.domain.contacts import phone_from_jid
from orchestrator.domain.recording_time import recorded_at_columns, sent_at_from_timestamp
from orchestrator.domain.whatsapp_notes import clean_text
from orchestrator.errors import ApiError
from orchestrator.repositories import events as events_repo
from orchestrator.repositories import jobs as jobs_repo
from orchestrator.security import new_id
from orchestrator.storage import delete_file_and_partials, raw_extension_for
from shared.layout import raw_file_name as build_raw_file_name
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
    # The WhatsApp message to react to / quote (domain/bot_replies.py).
    target: VideoMessageRef
    # React ✅ once the job exists. False when the webhook already answered
    # (low_res resend request, inbox hold notice: ⚠️), so that warning stays
    # visible on the video instead of being replaced by ✅.
    confirm_when_stored: bool = True
    # The sender's default prova (admin Contacts page), filled into the job.
    default_prova: str | None = None
    # The raw's readable name on workers (item 12), fixed when the bot got it.
    raw_file_name: str | None = None
    # When the filmmaker sent it (WhatsApp timestamp) and when the webhook
    # arrived: the fallbacks for the recording time (LIVESTACK-45).
    sent_at: datetime | None = None
    received_at: datetime | None = None
    # What the filmmaker typed with the video (LIVESTACK-49), kept with the job.
    caption: str | None = None


@dataclass
class WebhookOutcome:
    status_code: int
    response: IntakeResponse | None = None
    error: ApiError | None = None
    download: PendingMediaDownload | None = None
    # Answers to send after the DB work is done.
    replies: list[BotReply] = field(default_factory=list)


def ignored(reason: str, message_id: str | None = None) -> WebhookOutcome:
    response = IntakeResponse(job_id=None, message_id=message_id, ignored=True, reason=reason)
    return WebhookOutcome(status_code=200, response=response)


def handle_webhook(context: OrchestratorContext, payload: Any) -> WebhookOutcome:
    """Decide what to do with one webhook body. Does not download anything."""
    if isinstance(payload, dict) and payload.get("event") in intake_notes.CHANGE_EVENTS:
        # A sender edited or deleted a message (LIVESTACK-49).
        response = intake_notes.handle_change_event(context, payload)
        return WebhookOutcome(status_code=200, response=response)
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
    if key.is_group_or_broadcast:
        # Videos come by DM only (design §3). No job and no reply: the bot
        # must never post into a group it happens to be in.
        return ignored("group or broadcast chat; videos are accepted by DM only", key.id)
    incoming = find_incoming_video(webhook.data.message)
    with context.database.transaction() as connection:
        now = context.clock.now()
        # Everyone who writes is recorded, so the admin can allow or block
        # them with one click instead of typing numbers.
        contact_row = contacts.record_sender(
            connection, key.sender_jid, webhook.data.push_name, now
        )
        if incoming is None:
            # A text, a photo, an audio...: kept for the job page, never answered.
            response = intake_notes.keep_incoming_note(
                context, connection, webhook, payload, contact_row, now
            )
            return WebhookOutcome(status_code=200, response=response)
        decision = contacts.intake_decision(
            connection, contact_row, context.admin_settings.current.intake_mode, now
        )
        # Reactions and quotes go on the video's own message key; contacts
        # stay keyed by sender_jid (an @lid sender is matched by phone).
        target = VideoMessageRef(
            remote_jid=key.remote_jid,
            message_id=key.id,
            reply_jid=key.sender_jid,
            quoted_message=quoted_message_for(incoming),
        )
        if not decision.accept:
            outcome = ignored(decision.reason, key.id)
            if decision.send_refusal:
                # At most once per 24 h per sender (contacts.intake_decision).
                refusal = BotReply(target, REACTION_FAILED, (bot_messages.SENDER_NOT_AUTHORIZED,))
                outcome.replies.append(refusal)
            return outcome
        return _accept_incoming_video(
            context, connection, webhook, incoming, target, decision.default_prova, contact_row
        )


def _accept_incoming_video(
    context: OrchestratorContext,
    connection: sqlite3.Connection,
    webhook: EvolutionWebhook,
    incoming: IncomingMedia,
    target: VideoMessageRef,
    default_prova: str | None,
    contact_row: sqlite3.Row,
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
            replies.append(
                BotReply(target, REACTION_FAILED, (bot_messages.INBOX_FULL_RESEND_LATER,))
            )
        jobs_repo.save_intake_message(
            connection, key.id, REJECTED_INBOX_FULL, now, sender_jid=key.sender_jid
        )
        error = ApiError(
            413,
            ErrorCode.INBOX_FULL,
            "the VPS inbox is full; try again later",
            {"inbox_bytes": inbox_bytes, "inbox_cap_bytes": context.settings.inbox_cap_bytes},
        )
        return WebhookOutcome(status_code=413, error=error, replies=replies)

    warnings = []
    if low_res:
        warnings.append(bot_messages.LOW_RES_RESEND_AS_DOCUMENT)
    if inbox_bytes + incoming_bytes > context.admin_settings.inbox_hold_bytes:
        warnings.append(bot_messages.INBOX_NEARLY_FULL_PLEASE_HOLD)
    replies = [BotReply(target, REACTION_WARNING, tuple(warnings))] if warnings else []

    jobs_repo.save_intake_message(connection, key.id, DOWNLOADING, now, sender_jid=key.sender_jid)
    job_id = new_id()
    extension = raw_extension_for(incoming.media.file_name, incoming.media.mimetype)
    download = PendingMediaDownload(
        job_id=job_id,
        message_id=key.id,
        incoming=incoming,
        sender_jid=key.sender_jid,
        sender_name=webhook.data.push_name,
        raw_extension=extension,
        target=target,
        confirm_when_stored=not replies,
        default_prova=default_prova,
        # Sender = the contact's display name (admin-editable), else the phone.
        raw_file_name=build_raw_file_name(
            contact_row["display_name"], contact_row["phone"], now, job_id, extension
        ),
        sent_at=sent_at_from_timestamp(webhook.data.message_timestamp),
        received_at=now,
        caption=clean_text(incoming.media.caption),
    )
    response = IntakeResponse(job_id=None, message_id=key.id, accepted=True, low_res=low_res)
    return WebhookOutcome(status_code=202, response=response, download=download, replies=replies)


def send_replies(context: OrchestratorContext, replies: list[BotReply]) -> None:
    for reply in replies:
        send_bot_reply(context.evolution, reply)


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
        failed = BotReply(
            pending.target, REACTION_FAILED, (bot_messages.DOWNLOAD_FAILED_PLEASE_RESEND,)
        )
        send_bot_reply(context.evolution, failed)
        return None

    with context.database.transaction() as connection:
        now = context.clock.now()
        create_received_job(
            connection,
            now,
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
            prova=pending.default_prova,
            raw_file_name=pending.raw_file_name,
            sent_at=pending.sent_at,
            received_at=pending.received_at,
            caption=pending.caption,
        )
        # The same video as an earlier job: discarded at once (LIVESTACK-51).
        duplicate = duplicates.discard_if_duplicate(
            connection, pending.job_id, now, context.admin_settings.discard_grace
        )
    if duplicate is not None:
        # ⚠️ and "já tinha sido recebido (às 14:32)" instead of ✅, once:
        # a webhook retry of this message never gets here again.
        template = context.admin_settings.current.duplicate_video_message
        reply = duplicates.duplicate_reply(pending.target, duplicate, template, now)
        send_bot_reply(context.evolution, reply)
    elif pending.confirm_when_stored:
        # Confirm only now that the file is verified and the job exists.
        send_bot_reply(context.evolution, BotReply(pending.target, REACTION_STORED))
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
    prova: str | None = None,
    raw_file_name: str | None = None,
    sent_at: datetime | None = None,
    received_at: datetime | None = None,
    caption: str | None = None,
) -> None:
    """Insert a job in state `received` (its raw is already in the inbox).

    `prova` is the sender's default prova, if they have one; the reviewer
    sees it filled in and can change it. `raw_file_name` is the readable
    name workers store the raw under (item 12); None = built from
    `sender_name`, the JID's number and `now`.

    `sent_at` (WhatsApp's timestamp) and `received_at` (the webhook's
    arrival, default `now`) give the job its first recording time; the
    raw's own creation_time replaces it when the probe report arrives
    (domain/recording_time.py).
    """
    if received_at is None:
        received_at = now
    if raw_file_name is None:
        raw_file_name = build_raw_file_name(
            sender_name, phone_from_jid(sender_jid or ""), now, job_id, raw_extension
        )
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
            "prova": prova,
            "raw_file_name": raw_file_name,
            "sent_at": to_db_time(sent_at),
            "received_at": to_db_time(received_at),
            "caption": caption,
            **recorded_at_columns(received_at=received_at, sent_at=sent_at),
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
