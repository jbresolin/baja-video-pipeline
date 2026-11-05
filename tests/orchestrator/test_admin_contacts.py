"""WhatsApp senders (admin Contacts page) and how intake treats them.

Every sender who writes to the bot in a private chat is recorded as
`unknown` the first time. The admin then marks them `allowed` or
`blocked`. Together with the intake mode (Settings page) that decides
whether a video becomes a job:

    status \\ mode   open                allowlist
    allowed          job                 job
    unknown          job                 no job + "não autorizado" (once per 24 h)
    blocked          no job, no reply    no job, no reply

`send_video` (orchestrator/testing/scenarios.py) encrypts a small file like
WhatsApp, puts it on the fake CDN and posts the webhook, as Evolution would.
"""

import pytest

from orchestrator.domain import bot_messages
from orchestrator.domain.contacts import normalize_phone, phone_from_jid
from orchestrator.testing.scenarios import ensure_prova, job_id_for_message, send_video
from shared.testing.evolution_payloads import as_evolution_2_3_7_lid_payload, text_message_payload

PHONE = "5511987654321"


def contacts(harness, **params) -> list[dict]:
    response = harness.client.get("/admin/contacts", params=params, auth=harness.admin_auth)
    assert response.status_code == 200, response.text
    return response.json()["contacts"]


def set_contact(harness, phone=PHONE, **fields) -> dict:
    response = harness.client.patch(
        f"/admin/contacts/{phone}", json=fields, auth=harness.admin_auth
    )
    assert response.status_code == 200, response.text
    return response.json()


def set_intake_mode(harness, mode: str) -> None:
    response = harness.client.patch(
        "/admin/settings", json={"intake_mode": mode}, auth=harness.admin_auth
    )
    assert response.status_code == 200, response.text


def video_from(harness, phone=PHONE) -> tuple[str | None, dict]:
    """Send a video from `phone`; return (the job id or None, the webhook answer)."""
    response, payload = send_video(harness, sender_phone=phone)
    return job_id_for_message(harness, payload["data"]["key"]["id"]), response.json()


def replies_to(harness, phone=PHONE) -> list[str]:
    return harness.evolution.texts_to(phone)


def test_phone_numbers_are_digits_only():
    # From the webhook: the part before "@", without a device suffix.
    assert phone_from_jid("5511987654321@s.whatsapp.net") == "5511987654321"
    assert phone_from_jid("5511987654321:12@s.whatsapp.net") == "5511987654321"
    # Typed by an admin: punctuation is dropped; too short is refused.
    assert normalize_phone("+55 (11) 98765-4321") == "5511987654321"
    assert normalize_phone("1234") is None


def test_the_first_message_records_the_sender_as_unknown_with_their_profile_name(harness):
    # Any private message counts, not only videos: here a plain "oi".
    harness.client.post(
        "/intake/evolution-webhook",
        json=text_message_payload("oi", sender_phone=PHONE),
        headers=harness.webhook_headers,
    )
    [contact] = contacts(harness)
    assert contact["phone"] == PHONE
    assert contact["status"] == "unknown"
    # The display name is prefilled from the WhatsApp profile name (pushName).
    assert contact["display_name"] == contact["push_name"] == "Voluntário Teste"
    assert contact["first_seen_at"] == contact["last_seen_at"]


def test_a_name_typed_by_the_admin_is_never_overwritten_by_the_profile_name(harness):
    video_from(harness)
    set_contact(harness, display_name="Maria (equipe 42)")
    harness.clock.advance(60)
    video_from(harness)
    [contact] = contacts(harness)
    assert contact["display_name"] == "Maria (equipe 42)"
    # last_seen moved on with the second message.
    assert contact["last_seen_at"] > contact["first_seen_at"]


def test_group_messages_are_not_recorded(harness):
    # Design §19: groups are ignored entirely, so their members are not contacts.
    payload = text_message_payload("oi", sender_phone=PHONE)
    payload["data"]["key"]["remoteJid"] = "120363040000000000@g.us"
    harness.client.post("/intake/evolution-webhook", json=payload, headers=harness.webhook_headers)
    assert contacts(harness) == []


# --- Evolution 2.3.7 "@lid" senders ---
#
# Since Evolution 2.3.7 a private sender may arrive as an anonymous id
# ("53880532508775@lid") with the phone JID in key.remoteJidAlt. The
# contact must be the PHONE: that is what the admin sees, types and
# allows, and what the bot replies to. The @lid id is never a key.


def lid_video_from(harness, phone=PHONE) -> tuple[str | None, dict]:
    """Send a video in the Evolution 2.3.7 @lid shape (phone only in remoteJidAlt)."""
    response, payload = send_video(
        harness, sender_phone=phone, reshape=as_evolution_2_3_7_lid_payload
    )
    assert payload["data"]["key"]["remoteJid"].endswith("@lid")  # the shape under test
    return job_id_for_message(harness, payload["data"]["key"]["id"]), response.json()


def test_a_lid_sender_is_recorded_under_the_phone_number(harness):
    lid_video_from(harness)

    [contact] = contacts(harness)
    # The phone from remoteJidAlt, not the anonymous @lid number.
    assert contact["phone"] == PHONE
    assert "53880532508775" not in str(contacts(harness))


def test_a_lid_sender_and_a_classic_one_are_the_same_contact(harness):
    # The same person, once in each shape: one contact, not two.
    video_from(harness)
    lid_video_from(harness)
    assert [contact["phone"] for contact in contacts(harness)] == [PHONE]


def test_a_blocked_phone_is_refused_when_it_writes_through_a_lid(harness):
    # The admin blocked the phone number; the video comes in the @lid shape.
    harness.client.post(
        "/admin/contacts", json={"phone": PHONE, "status": "blocked"}, auth=harness.admin_auth
    )
    job_id, answer = lid_video_from(harness)
    assert job_id is None
    assert answer["ignored"] is True
    assert harness.evolution.sent == []


def test_an_allowed_phone_gets_through_allowlist_mode_through_a_lid(harness):
    # Allowlist mode: only the phone the admin allowed may send. Matching on
    # the @lid id instead would refuse a person the admin approved.
    harness.client.post(
        "/admin/contacts", json={"phone": PHONE, "status": "allowed"}, auth=harness.admin_auth
    )
    set_intake_mode(harness, "allowlist")

    job_id, _ = lid_video_from(harness)

    assert job_id is not None
    # And a phone that is not allowed is refused, with the reply sent to
    # the phone (sendText needs a number, not a lid).
    other = "5511900002222"
    refused, _ = lid_video_from(harness, phone=other)
    assert refused is None
    assert harness.evolution.texts_to(other) == [bot_messages.SENDER_NOT_AUTHORIZED]


# The six combinations of the table in the module docstring:
# (status the admin set, intake mode, job expected?, refusal reply expected?)
INTAKE_CASES = [
    ("allowed", "open", True, False),
    ("unknown", "open", True, False),
    ("blocked", "open", False, False),
    ("allowed", "allowlist", True, False),
    ("unknown", "allowlist", False, True),
    ("blocked", "allowlist", False, False),
]


@pytest.mark.parametrize(("status", "mode", "job_expected", "refusal_expected"), INTAKE_CASES)
def test_intake_by_contact_status_and_intake_mode(
    harness, status, mode, job_expected, refusal_expected
):
    # Set up: the sender exists with the given status, and the mode is set.
    harness.client.post(
        "/admin/contacts", json={"phone": PHONE, "status": status}, auth=harness.admin_auth
    )
    set_intake_mode(harness, mode)

    job_id, answer = video_from(harness)

    assert (job_id is not None) == job_expected
    if not job_expected:
        # Refused videos are acknowledged (200 ignored) so Evolution does
        # not retry them for 20 minutes.
        assert answer["ignored"] is True
    refusal_sent = bot_messages.SENDER_NOT_AUTHORIZED in replies_to(harness)
    assert refusal_sent == refusal_expected
    if status == "blocked":
        # A blocked sender hears nothing at all.
        assert replies_to(harness) == []


def test_the_refusal_reply_is_sent_at_most_once_per_24_hours(harness):
    set_intake_mode(harness, "allowlist")
    # Three videos within a day: one reply.
    for _ in range(3):
        video_from(harness)
        harness.clock.advance(3600)
    assert replies_to(harness).count(bot_messages.SENDER_NOT_AUTHORIZED) == 1
    # A day after the reply, the next video gets it again.
    harness.clock.advance(24 * 3600)
    video_from(harness)
    assert replies_to(harness).count(bot_messages.SENDER_NOT_AUTHORIZED) == 2


def test_an_unknown_sender_who_only_says_hello_gets_no_reply_in_allowlist_mode(harness):
    # Only videos are refused out loud; chat is ignored as before.
    set_intake_mode(harness, "allowlist")
    harness.client.post(
        "/intake/evolution-webhook",
        json=text_message_payload("oi", sender_phone=PHONE),
        headers=harness.webhook_headers,
    )
    assert replies_to(harness) == []


def test_allowing_a_refused_sender_lets_their_next_video_through(harness):
    # The one-click flow from the brief: the sender wrote, was refused,
    # the admin finds them on the Contacts page and allows them.
    set_intake_mode(harness, "allowlist")
    assert video_from(harness)[0] is None
    [contact] = contacts(harness, status="unknown")
    set_contact(harness, contact["phone"], status="allowed")
    assert video_from(harness)[0] is not None


def test_a_default_prova_is_filled_into_the_senders_new_jobs(harness):
    prova = ensure_prova(harness, "Enduro")
    harness.client.post(
        "/admin/contacts",
        json={"phone": PHONE, "status": "allowed", "default_prova_id": prova["id"]},
        auth=harness.admin_auth,
    )
    job_id, _ = video_from(harness)
    assert harness.job(job_id)["prova"] == "Enduro"
    # Another sender without a default prova: the field stays empty.
    other_job, _ = video_from(harness, phone="5511900001111")
    assert harness.job(other_job)["prova"] is None


def test_the_contact_list_shows_the_default_provas_name_and_null_clears_fields(harness):
    prova = ensure_prova(harness, "Suspensão")
    harness.client.post(
        "/admin/contacts",
        json={"phone": "+55 11 98765-4321", "default_prova_id": prova["id"], "notes": "capitã"},
        auth=harness.admin_auth,
    )
    [contact] = contacts(harness)
    # The typed number was stored as digits; a manual contact is "allowed"
    # by default and has never written yet.
    assert (contact["phone"], contact["status"], contact["first_seen_at"]) == (
        PHONE,
        "allowed",
        None,
    )
    assert contact["default_prova"] == "Suspensão"
    cleared = set_contact(harness, default_prova_id=None, notes=None)
    assert (cleared["default_prova"], cleared["notes"]) == (None, None)
    # Leaving a field out keeps it: the status did not change.
    assert cleared["status"] == "allowed"


def test_bad_contact_edits_are_refused(harness):
    video_from(harness)
    unknown_prova = harness.client.patch(
        f"/admin/contacts/{PHONE}", json={"default_prova_id": 999}, auth=harness.admin_auth
    )
    assert unknown_prova.status_code == 400
    missing = harness.client.patch(
        "/admin/contacts/5500000000000", json={"status": "allowed"}, auth=harness.admin_auth
    )
    assert missing.status_code == 404
    duplicate = harness.client.post(
        "/admin/contacts", json={"phone": PHONE}, auth=harness.admin_auth
    )
    assert duplicate.json()["error"]["code"] == "already_exists"


def test_contact_changes_are_audited(harness):
    video_from(harness)
    set_contact(harness, status="blocked")
    [event] = harness.client.get("/admin/audit?area=contacts", auth=harness.admin_auth).json()[
        "events"
    ]
    assert (event["kind"], event["actor"]) == ("contact_updated", "admin:joao")
    assert event["details"]["before"]["status"] == "unknown"
    assert event["details"]["after"]["status"] == "blocked"
