"""The sender's messages against the REAL orchestrator (LIVESTACK-49).

The other page tests use the fake orchestrator. This one runs the review
UI on top of the real one (orchestrator.testing.harness), so the whole
path is exercised: Evolution 2.3.7 webhooks (the "@lid" sender shape and
Long timestamps) go into intake, the orchestrator decides which messages
belong to which video, and the UI shows them on the job page and counts
them in the queue. Then the filmmaker deletes a message, and the admin
changes how long messages are kept.
"""

from datetime import UTC, datetime

import pytest

from orchestrator.testing.harness import (
    ADMIN_NAME,
    ADMIN_PASSWORD,
    REVIEWER_NAME,
    REVIEWER_PASSWORD,
    build_harness,
)
from orchestrator.testing.scenarios import post_webhook, receive_job
from shared.testing.evolution_payloads import (
    as_evolution_2_3_7_lid_payload,
    edited_event_payload,
    media_note_payload,
    text_message_payload,
)
from tests.review_ui.admin_browser import follow, post_form, signed_in_browser

MARIA = "5511987650001"


@pytest.fixture
def harness(tmp_path):
    """The real orchestrator on 2026-03-26 at 14:00 on the track (17:00 UTC)."""
    built = build_harness(tmp_path / "data", clock_start=datetime(2026, 3, 26, 17, 0, tzinfo=UTC))
    yield built
    built.client.close()
    built.context.database.close()


def maria_writes(harness, text, *, quoting=None, message_id=None):
    harness.clock.advance(60)
    payload = text_message_payload(
        text, MARIA, sender_name="Maria", message_id=message_id, quoted_message_id=quoting
    )
    post_webhook(harness, payload, reshape=as_evolution_2_3_7_lid_payload)


def as_maria(payload):
    """Her WhatsApp profile name on the video too, in the @lid shape."""
    payload["data"]["pushName"] = "Maria"
    return as_evolution_2_3_7_lid_payload(payload)


def maria_sends_a_video(harness, caption=None):
    harness.clock.advance(60)
    job_id = receive_job(harness, sender_phone=MARIA, caption=caption, reshape=as_maria)
    return job_id, harness.job(job_id)["source"]["message_id"]


def test_what_maria_wrote_reaches_the_right_job_page(harness):
    # 14:01 "vou mandar o 42", 14:02 first video (caption "42 p1"),
    # 14:03 an audio, 14:04 second video, 14:05 a reply to the FIRST video,
    # 14:06 a text about the second one (later deleted).
    maria_writes(harness, "vou mandar o 42")
    first, first_message_id = maria_sends_a_video(harness, caption="42 p1")
    harness.clock.advance(60)
    post_webhook(harness, media_note_payload("audioMessage", MARIA))
    second, _ = maria_sends_a_video(harness)
    maria_writes(harness, "errei, é a passagem 2", quoting=first_message_id)
    maria_writes(harness, "esse é o 17", message_id="3EB0MARIA017")

    browser = signed_in_browser(harness, REVIEWER_NAME, REVIEWER_PASSWORD)

    # The first video: caption, the reply (so the section opens by
    # itself), the audio, and the announcement under "Antes deste vídeo".
    page = browser.get(f"/review/jobs/{first}").text
    assert '<details class="sender-messages" id="sender-messages" open>' in page
    assert "Mensagens de Maria (4)" in page
    for words in ("42 p1", "errei, é a passagem 2", "(áudio)", "vou mandar o 42"):
        assert words in page
    assert "Antes deste vídeo (1)" in page
    # The second video's page never shows the reply meant for the first.
    second_page = browser.get(f"/review/jobs/{second}").text
    assert "errei, é a passagem 2" not in second_page
    assert "esse é o 17" in second_page and "Mensagens de Maria (2)" in second_page

    # The queue's speech bubbles carry the same counts.
    queue = browser.get("/review/?state=received").text
    assert "💬 4" in queue and "💬 2" in queue

    # Maria deletes "esse é o 17" for everyone: the text goes, the line stays.
    post_webhook(harness, edited_event_payload("3EB0MARIA017", None, sender_phone=MARIA))
    second_page = browser.get(f"/review/jobs/{second}").text
    assert "esse é o 17" not in second_page and "apagada" in second_page


def test_the_admin_sets_how_long_messages_are_kept(harness):
    # Configurações has the keep time, 14 days to start with.
    admin = signed_in_browser(harness, ADMIN_NAME, ADMIN_PASSWORD)
    page = admin.get("/review/admin/settings").text
    assert 'name="sender_messages_keep_days"' in page
    assert harness.context.admin_settings.current.sender_messages_keep_days == 14

    # The admin saves the form with 30 days (the other fields as they were).
    form = {
        "intake_mode": "open",
        "inbox_hold_gb": "22",
        "proxy_evict_min_free_gb": "10",
        "installer_download_url": "",
    }
    follow(
        admin, post_form(admin, "/review/admin/settings", **form, sender_messages_keep_days="30")
    )
    assert harness.context.admin_settings.current.sender_messages_keep_days == 30

    # Days are whole: "1,5" is refused with the form's error, nothing changes.
    refused = post_form(admin, "/review/admin/settings", **form, sender_messages_keep_days="1,5")
    assert refused.status_code == 400
    assert harness.context.admin_settings.current.sender_messages_keep_days == 30
