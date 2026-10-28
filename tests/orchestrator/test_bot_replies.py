"""Tests for what the Baja Bot answers a volunteer, situation by situation.

The texts live in orchestrator/domain/bot_messages.py and are quoted in
docs/volunteers-pt-BR.md, the message pinned in the volunteers' group. For
each situation below we send one video the way WhatsApp would and check
the volunteer got exactly the expected message(s), word for word, and
nothing else. "Exactly" matters: a volunteer who gets two contradictory
answers for one video will not know what to do.
"""

from pathlib import Path

from orchestrator.domain import bot_messages
from orchestrator.testing.harness import build_harness
from orchestrator.testing.scenarios import SAMPLE_VIDEO_BYTES, receive_job, send_video
from shared.testing.evolution_payloads import document_message_payload
from shared.testing.whatsapp_media_encrypt import encrypt_bytes

VOLUNTEER_PHONE = "5511977776666"
VOLUNTEER_GUIDE = Path(__file__).resolve().parents[2] / "docs" / "volunteers-pt-BR.md"


def test_a_good_document_gets_one_received_confirmation(harness):
    # A video sent the right way (as a document) that passes every check.
    receive_job(harness, sender_phone=VOLUNTEER_PHONE)
    # One message: the confirmation, sent after the job was created.
    assert harness.evolution.texts_to(VOLUNTEER_PHONE) == [bot_messages.RECEIVED_OK]


def test_a_whatsapp_video_gets_only_the_resend_as_document_request(harness):
    # The same video sent as a normal WhatsApp *video* (recompressed). It is
    # kept (flagged low_res), and the bot asks for the original as a document.
    receive_job(harness, as_video_message=True, sender_phone=VOLUNTEER_PHONE)
    # Only the resend request: no second "received" message on top of it.
    assert harness.evolution.texts_to(VOLUNTEER_PHONE) == [bot_messages.LOW_RES_RESEND_AS_DOCUMENT]


def test_above_the_soft_cap_the_volunteer_only_hears_please_hold(tmp_path):
    # Soft cap 1 kB, hard cap 1 MB: the sample video lands between the two,
    # so it is accepted but the inbox counts as "nearly full".
    harness = build_harness(tmp_path, inbox_hold_gb=0.000001, inbox_cap_gb=0.001)
    receive_job(harness, sender_phone=VOLUNTEER_PHONE)
    # The hold notice already says "Recebemos seu vídeo!", so no extra confirmation.
    assert harness.evolution.texts_to(VOLUNTEER_PHONE) == [
        bot_messages.INBOX_NEARLY_FULL_PLEASE_HOLD
    ]


def test_above_the_hard_cap_the_volunteer_is_told_to_resend_later(tmp_path):
    # Hard cap 10 kB and a 20 kB video: refused before any download.
    harness = build_harness(tmp_path, inbox_hold_gb=0.000001, inbox_cap_gb=0.00001)
    response, _ = send_video(harness, b"x" * 20_000, sender_phone=VOLUNTEER_PHONE)
    assert response.status_code == 413
    assert harness.evolution.texts_to(VOLUNTEER_PHONE) == [bot_messages.INBOX_FULL_RESEND_LATER]


def test_a_failed_download_gets_only_the_failure_message(harness):
    # Corrupt the encrypted file on the fake WhatsApp CDN (flip its first
    # byte), so the hash check after the download fails.
    blob, encrypted = encrypt_bytes(SAMPLE_VIDEO_BYTES)
    url = harness.cdn.add_blob("/v/broken.enc", bytes([blob[0] ^ 0xFF]) + blob[1:])
    payload = document_message_payload(
        encrypted, url, "/v/broken.enc", sender_phone=VOLUNTEER_PHONE
    )
    harness.client.post("/intake/evolution-webhook", json=payload, headers=harness.webhook_headers)
    # The failure message, and no "received" confirmation: no job exists.
    assert harness.evolution.texts_to(VOLUNTEER_PHONE) == [
        bot_messages.DOWNLOAD_FAILED_PLEASE_RESEND
    ]


def test_every_reply_is_short_distinct_and_asks_for_a_document_when_resending():
    # Volunteers read these on a phone in the middle of a race: keep each
    # one to a couple of lines (WhatsApp shows ~200 characters without "ler mais").
    assert all(0 < len(text) <= 200 for text in bot_messages.ALL_REPLIES)
    # No two situations share a text, so a volunteer can tell them apart.
    assert len(set(bot_messages.ALL_REPLIES)) == len(bot_messages.ALL_REPLIES)
    # Both "send it again" replies say *how*: as a document (design §3).
    for text in (
        bot_messages.LOW_RES_RESEND_AS_DOCUMENT,
        bot_messages.DOWNLOAD_FAILED_PLEASE_RESEND,
    ):
        assert "*documento*" in text


def test_the_volunteer_guide_quotes_every_reply_word_for_word():
    # docs/volunteers-pt-BR.md explains each reply to the volunteers. If
    # someone rewords a reply in bot_messages.py, this fails until the
    # guide is updated too, so volunteers never read about a message the
    # bot no longer sends.
    guide = VOLUNTEER_GUIDE.read_text(encoding="utf-8")
    missing = [text for text in bot_messages.ALL_REPLIES if text not in guide]
    assert missing == []
