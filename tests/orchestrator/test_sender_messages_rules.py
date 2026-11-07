"""Whose messages are kept, edits, deletions and how long they stay (LIVESTACK-49).

The sender's messages follow the same rule as their videos: a blocked
contact's are never kept, and in allowlist mode only allowed contacts'
are. Edits replace the text (marked "editada"), deletions for everyone
drop it (marked "apagada"). After the Settings' sender_messages_keep_days
(14 by default) the retention sweep deletes them.

Edits and deletions are posted the way Evolution 2.3.7 really sends them
(read from its source): never as messages.upsert, but as the event
"messages.edited" whose data is WhatsApp's protocolMessage (for an edit
and for a deletion), and, for a deletion, also "messages.delete".
"""

from datetime import timedelta

import pytest

from orchestrator.background.sweepers import delete_old_sender_messages_once
from orchestrator.testing.scenarios import post_webhook, receive_job, sender_messages
from shared.testing.evolution_payloads import (
    delete_event_payload,
    edited_event_payload,
    text_message_payload,
)

PHONE = "5511987650001"


def video_then_text(harness, text="carro 42", message_id="3EB0TEXT0001"):
    """A video, then (a minute later) a text from the same sender. Returns the job id."""
    job_id = receive_job(harness, sender_phone=PHONE)
    harness.clock.advance(60)
    post_webhook(harness, text_message_payload(text, PHONE, message_id=message_id))
    return job_id


def set_contact(harness, status):
    response = harness.client.post(
        "/admin/contacts", json={"phone": PHONE, "status": status}, auth=harness.admin_auth
    )
    assert response.status_code == 201, response.text


def set_settings(harness, **values):
    response = harness.client.patch("/admin/settings", json=values, auth=harness.admin_auth)
    assert response.status_code == 200, response.text


def test_a_blocked_contacts_texts_are_not_kept(harness):
    # The admin blocked this number. Their video makes no job, and their
    # text must not be stored either (nothing of a blocked sender is kept).
    set_contact(harness, "blocked")
    answer = post_webhook(harness, text_message_payload("carro 42", PHONE))
    assert answer["ignored"] is True and answer["message_stored"] is False
    # Unblocked later: the earlier text is still not there, only new ones.
    harness.client.patch(
        f"/admin/contacts/{PHONE}", json={"status": "allowed"}, auth=harness.admin_auth
    ).raise_for_status()
    job_id = video_then_text(harness, "carro 7")
    assert [item["text"] for item in sender_messages(harness, job_id)] == ["carro 7"]


@pytest.mark.parametrize(
    ("status", "kept"),
    [("unknown", False), ("allowed", True)],
)
def test_in_allowlist_mode_only_allowed_contacts_texts_are_kept(harness, status, kept):
    set_contact(harness, status)
    set_settings(harness, intake_mode="allowlist")
    answer = post_webhook(harness, text_message_payload("carro 42", PHONE))
    assert answer["message_stored"] is kept
    # A text never gets a reply, not even the allowlist refusal that a
    # video from an unallowed sender gets.
    assert harness.evolution.sent == []


@pytest.mark.parametrize("type_as_name", [False, True])
def test_an_edit_replaces_the_text_and_marks_it_edited(harness, type_as_name):
    job_id = video_then_text(harness, "carro 24")
    # "Editar" on the phone: Evolution sends messages.edited with the new text.
    # Baileys may write the type as 14 or as "MESSAGE_EDIT": both work.
    harness.clock.advance(30)
    answer = post_webhook(
        harness,
        edited_event_payload("3EB0TEXT0001", "carro 42", type_as_name=type_as_name),
    )
    assert answer["message_stored"] is True
    [item] = sender_messages(harness, job_id)
    assert (item["text"], item["edited"], item["deleted"]) == ("carro 42", True, False)


@pytest.mark.parametrize("type_as_name", [False, True])
def test_a_deletion_for_everyone_hides_the_text_and_marks_it_deleted(harness, type_as_name):
    job_id = video_then_text(harness, "mensagem errada")
    # "Apagar para todos": messages.edited with type REVOKE (0)...
    post_webhook(harness, edited_event_payload("3EB0TEXT0001", None, type_as_name=type_as_name))
    # ...and, when Evolution has the message in its store, messages.delete too.
    post_webhook(harness, delete_event_payload("3EB0TEXT0001", PHONE))
    [item] = sender_messages(harness, job_id)
    # Still listed (the reviewer sees something was said), without the text.
    assert (item["text"], item["deleted"]) == (None, True)
    # An edit arriving after the deletion does not bring the text back.
    post_webhook(harness, edited_event_payload("3EB0TEXT0001", "outra coisa"))
    assert sender_messages(harness, job_id)[0]["text"] is None


def test_messages_delete_alone_is_enough(harness):
    # If only messages.delete arrives (a different Evolution build), it still works.
    job_id = video_then_text(harness)
    answer = post_webhook(harness, delete_event_payload("3EB0TEXT0001", PHONE))
    assert answer["message_stored"] is True
    assert sender_messages(harness, job_id)[0]["deleted"] is True


def test_an_edit_sent_as_an_upsert_is_applied_too(harness):
    # Another Evolution version could upsert the edit instead (with the
    # sender's key); it is applied, not stored as a new message.
    job_id = video_then_text(harness, "carro 24")
    upsert = text_message_payload("x", PHONE, message_id="3EB0EDIT0001")
    upsert["data"]["message"] = {
        "protocolMessage": {
            "key": {"remoteJid": f"{PHONE}@s.whatsapp.net", "fromMe": True, "id": "3EB0TEXT0001"},
            "type": 14,
            "editedMessage": {"conversation": "carro 42"},
        }
    }
    post_webhook(harness, upsert)
    assert [(item["text"], item["edited"]) for item in sender_messages(harness, job_id)] == [
        ("carro 42", True)
    ]


def test_an_edit_of_an_unknown_message_is_ignored(harness):
    # A message from before this feature, or one of the bot's own.
    answer = post_webhook(harness, edited_event_payload("3EB0NEVERSEEN", "oi"))
    assert answer["ignored"] is True and answer["message_stored"] is False


def test_the_bots_own_messages_and_group_chats_are_not_kept(harness):
    job_id = receive_job(harness, sender_phone=PHONE)
    own = text_message_payload("✅", PHONE)
    own["data"]["key"]["fromMe"] = True
    assert post_webhook(harness, own)["message_stored"] is False
    group = text_message_payload("carro 42 no grupo", PHONE)
    group["data"]["key"]["remoteJid"] = "120363000000000000@g.us"
    assert post_webhook(harness, group)["message_stored"] is False
    assert sender_messages(harness, job_id) == []


def test_the_retention_sweep_deletes_messages_older_than_the_setting(harness):
    job_id = receive_job(harness, sender_phone=PHONE, caption="carro 42")
    harness.clock.advance(60)
    post_webhook(harness, text_message_payload("antigo", PHONE))
    # 14 days by default: a day short of that, the sweep deletes nothing.
    harness.clock.advance(timedelta(days=13).total_seconds())
    post_webhook(harness, text_message_payload("recente", PHONE))
    assert delete_old_sender_messages_once(harness.context) == 0
    # Two more days: the first text is 15 days old and goes; the second stays.
    harness.clock.advance(timedelta(days=2).total_seconds())
    assert delete_old_sender_messages_once(harness.context) == 1
    texts = [item["text"] for item in sender_messages(harness, job_id)]
    # The video's caption lives on the job and is never swept.
    assert texts == ["carro 42", "recente"]


def test_the_keep_time_is_a_setting(harness):
    settings = harness.client.get("/admin/settings", auth=harness.admin_auth).json()
    assert settings["settings"]["sender_messages_keep_days"] == 14
    set_settings(harness, sender_messages_keep_days=2)
    post_webhook(harness, text_message_payload("antigo", PHONE))
    harness.clock.advance(timedelta(days=3).total_seconds())
    assert delete_old_sender_messages_once(harness.context) == 1
    # Out of range (0 would delete everything at once) → 400.
    refused = harness.client.patch(
        "/admin/settings", json={"sender_messages_keep_days": 0}, auth=harness.admin_auth
    )
    assert refused.status_code == 400
