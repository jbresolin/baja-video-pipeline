"""Tests for what the Baja Bot answers a volunteer, situation by situation.

Volunteers send several files in a row, so every answer is attached to
the video it is about (domain/bot_replies.py):

* a reaction on the video: ✅ stored, ⚠️ kept with a warning, ❌ not kept;
* any text is a *quoted reply* to that video, sent to the phone number.

The texts live in orchestrator/domain/bot_messages.py and are quoted in
docs/volunteers-pt-BR.md, the message pinned in the volunteers' group. For
each situation below we send one video the way WhatsApp would and check
the volunteer got exactly the expected reaction and text(s), word for
word, and nothing else. "Exactly" matters: a volunteer who gets two
contradictory answers for one video will not know what to do.
"""

from pathlib import Path

from orchestrator.domain import bot_messages
from orchestrator.testing.harness import build_harness
from orchestrator.testing.scenarios import SAMPLE_VIDEO_BYTES, receive_job, send_video
from shared.testing.evolution_payloads import (
    as_evolution_2_3_7_lid_payload,
    document_message_payload,
)
from shared.testing.whatsapp_media_encrypt import encrypt_bytes

VOLUNTEER_PHONE = "5511977776666"
VOLUNTEER_GUIDE = Path(__file__).resolve().parents[2] / "docs" / "volunteers-pt-BR.md"


def message_id_of(payload) -> str:
    return payload["data"]["key"]["id"]


def assert_every_text_quotes(harness, payload):
    """Each text the bot sent quotes this video's message key, as it arrived."""
    key = payload["data"]["key"]
    for text in harness.evolution.sent:
        assert text.quoted["key"] == {
            "remoteJid": key["remoteJid"],
            "fromMe": False,
            "id": key["id"],
        }


def test_a_good_document_gets_a_check_mark_and_no_text(harness):
    # A video sent the right way (as a document) that passes every check.
    _, payload = send_video(harness, sender_phone=VOLUNTEER_PHONE)
    # One answer: ✅ on that video, sent after the job was created. No text,
    # so five videos in a row do not produce five loose "received" messages.
    assert harness.evolution.reactions_on(message_id_of(payload)) == ["✅"]
    assert harness.evolution.texts_to(VOLUNTEER_PHONE) == []
    # Evolution would have accepted every request the bot made.
    assert harness.evolution.problems == []


def test_a_whatsapp_video_gets_a_warning_and_a_quoted_resend_request(harness):
    # The same video sent as a normal WhatsApp *video* (recompressed). It is
    # kept (flagged low_res), and the bot asks for the original as a document.
    _, payload = send_video(harness, as_video_message=True, sender_phone=VOLUNTEER_PHONE)
    # ⚠️ on the video (and no ✅ replacing it later), plus the request...
    assert harness.evolution.reactions_on(message_id_of(payload)) == ["⚠️"]
    assert harness.evolution.texts_to(VOLUNTEER_PHONE) == [bot_messages.LOW_RES_RESEND_AS_DOCUMENT]
    # ...as a reply quoting that video, so the volunteer knows which one.
    assert_every_text_quotes(harness, payload)
    [text] = harness.evolution.sent
    assert "videoMessage" in text.quoted["message"]


def test_above_the_soft_cap_the_volunteer_gets_a_quoted_please_hold(tmp_path):
    # Soft cap 1 kB, hard cap 1 MB: the sample video lands between the two,
    # so it is accepted but the inbox counts as "nearly full".
    harness = build_harness(tmp_path, inbox_hold_gb=0.000001, inbox_cap_gb=0.001)
    _, payload = send_video(harness, sender_phone=VOLUNTEER_PHONE)
    # ⚠️ stays on the video: the hold notice already says "Recebemos este vídeo!".
    assert harness.evolution.reactions_on(message_id_of(payload)) == ["⚠️"]
    assert harness.evolution.texts_to(VOLUNTEER_PHONE) == [
        bot_messages.INBOX_NEARLY_FULL_PLEASE_HOLD
    ]
    assert_every_text_quotes(harness, payload)


def test_above_the_hard_cap_the_volunteer_is_told_to_resend_later(tmp_path):
    # Hard cap 10 kB and a 20 kB video: refused before any download.
    harness = build_harness(tmp_path, inbox_hold_gb=0.000001, inbox_cap_gb=0.00001)
    response, payload = send_video(harness, b"x" * 20_000, sender_phone=VOLUNTEER_PHONE)
    assert response.status_code == 413
    # ❌: this video was NOT kept.
    assert harness.evolution.reactions_on(message_id_of(payload)) == ["❌"]
    assert harness.evolution.texts_to(VOLUNTEER_PHONE) == [bot_messages.INBOX_FULL_RESEND_LATER]
    assert_every_text_quotes(harness, payload)


def test_a_failed_download_gets_a_cross_and_the_quoted_failure_message(harness):
    # Corrupt the encrypted file on the fake WhatsApp CDN (flip its first
    # byte), so the hash check after the download fails.
    blob, encrypted = encrypt_bytes(SAMPLE_VIDEO_BYTES)
    url = harness.cdn.add_blob("/v/broken.enc", bytes([blob[0] ^ 0xFF]) + blob[1:])
    payload = document_message_payload(
        encrypted, url, "/v/broken.enc", sender_phone=VOLUNTEER_PHONE
    )
    harness.client.post("/intake/evolution-webhook", json=payload, headers=harness.webhook_headers)
    # ❌ and the failure message; no ✅: no job exists.
    assert harness.evolution.reactions_on(message_id_of(payload)) == ["❌"]
    assert harness.evolution.texts_to(VOLUNTEER_PHONE) == [
        bot_messages.DOWNLOAD_FAILED_PLEASE_RESEND
    ]
    assert_every_text_quotes(harness, payload)
    # The quote shows the document's name in WhatsApp's "reply to" preview.
    [text] = harness.evolution.sent
    assert text.quoted["message"]["documentMessage"]["fileName"] == "VID_20261025_101500.mp4"


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
    # The guide also explains the three reactions.
    for emoji in ("✅", "⚠️", "❌"):
        assert emoji in guide


def test_a_real_2_3_7_lid_document_is_reacted_to_on_its_lid_key(harness):
    # Regression for the first real video (2026-11-04): Evolution 2.3.7 sent
    # the sender as an anonymous "@lid" id, the phone in remoteJidAlt, and
    # fileLength as a Long object. Now: the job exists, and the ✅ reaction
    # uses the message key exactly as it arrived, @lid included, because a
    # reaction addresses the message, not the person.
    _, payload = send_video(
        harness, sender_phone=VOLUNTEER_PHONE, reshape=as_evolution_2_3_7_lid_payload
    )
    [reaction] = harness.evolution.reactions
    assert reaction.key == {
        "remoteJid": "53880532508775@lid",
        "fromMe": False,
        "id": message_id_of(payload),
    }
    assert reaction.reaction == "✅"


def test_a_lid_video_message_gets_its_text_on_the_phone_and_its_quote_on_the_lid(harness):
    # A low_res video from a LID sender: the text goes to the phone number
    # (sendText cannot deliver to a LID), while the quote and the reaction
    # point at the message's own @lid key.
    receive_job(
        harness,
        as_video_message=True,
        sender_phone=VOLUNTEER_PHONE,
        reshape=as_evolution_2_3_7_lid_payload,
    )
    assert harness.evolution.texts_to(VOLUNTEER_PHONE) == [bot_messages.LOW_RES_RESEND_AS_DOCUMENT]
    [text] = harness.evolution.sent
    assert text.quoted["key"]["remoteJid"] == "53880532508775@lid"
    assert harness.evolution.reactions[0].key["remoteJid"] == "53880532508775@lid"


def test_an_unauthorized_sender_gets_a_cross_and_a_quoted_refusal_once_a_day(harness):
    # Allowlist mode (admin Settings page): only senders the admin allowed on
    # the Contacts page may send. This volunteer was never allowed, and the
    # video arrives in the Evolution 2.3.7 "@lid" shape.
    response = harness.client.patch(
        "/admin/settings", json={"intake_mode": "allowlist"}, auth=harness.admin_auth
    )
    assert response.status_code == 200, response.text
    _, payload = send_video(
        harness, sender_phone=VOLUNTEER_PHONE, reshape=as_evolution_2_3_7_lid_payload
    )

    # Like every other "not kept": ❌ on that video, on its own @lid key...
    [reaction] = harness.evolution.reactions
    assert reaction.key["remoteJid"] == "53880532508775@lid"
    assert reaction.reaction == "❌"
    # ...and the refusal as a quoted reply, sent to the phone number.
    assert harness.evolution.texts_to(VOLUNTEER_PHONE) == [bot_messages.SENDER_NOT_AUTHORIZED]
    assert_every_text_quotes(harness, payload)
    assert harness.evolution.problems == []

    # A second video the same day: no reply at all, not even a reaction, so
    # the bot cannot be used to spam a number (once per 24 h, contacts.py).
    harness.clock.advance(3600)
    send_video(harness, sender_phone=VOLUNTEER_PHONE, reshape=as_evolution_2_3_7_lid_payload)
    assert len(harness.evolution.reactions) == 1
    assert len(harness.evolution.sent) == 1
