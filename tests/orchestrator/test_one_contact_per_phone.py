"""One person, one contact, however the number is written (LIVESTACK-51 item 2).

The bug, found on the production data after the 2026-11-07 test: the admin
typed a filmmaker's number as people write it today, with the mobile 9th
digit, and gave him a default prova. WhatsApp's id (JID) of that older
number has no 9th digit, so intake recorded his videos under a *second*
contact ("unknown", no default prova) and none of them got the prova. The
existing test sent the video from exactly the digits the admin typed, so
it could not see this.

The rule now (orchestrator/domain/phones.py): a Brazilian mobile is kept
as 55 + DDD + 9 + 8 digits, whether it arrives with or without the 9th
digit, and a number typed without the country code gets 55 in front.

Also here: a default prova set later can be applied to the sender's videos
still waiting for review without a prova (the page asks first).

`send_video` (orchestrator/testing/scenarios.py) posts a WhatsApp webhook
as Evolution does, from `sender_phone` + "@s.whatsapp.net".
"""

import pytest

from orchestrator.domain.phones import canonical_phone_from_jid_digits, canonical_typed_phone
from orchestrator.testing.scenarios import (
    ensure_prova,
    hold_raw,
    job_id_for_message,
    make_proxy,
    review_to_ready,
    send_video,
)
from shared.models import WorkerRole

# Maria (filmagem), Campinas: a mobile, written today with the 9th digit.
MARIA_TODAY = "5519981234567"
# ...and the same phone as WhatsApp identifies older numbers: no 9th digit.
MARIA_JID = "551981234567"


# --- The rule ---


@pytest.mark.parametrize(
    "typed",
    [
        "+55 19 98123-4567",  # as people write it today
        "+55 (19) 8123-4567",  # the old way, without the 9th digit
        "19 98123-4567",  # without the country code
        "(19) 8123-4567",  # without both
        "5519981234567",
    ],
)
def test_every_way_of_typing_a_mobile_is_the_same_contact(typed):
    assert canonical_typed_phone(typed) == MARIA_TODAY


def test_a_jid_with_or_without_the_ninth_digit_is_the_same_contact():
    assert canonical_phone_from_jid_digits(MARIA_JID) == MARIA_TODAY
    assert canonical_phone_from_jid_digits(MARIA_TODAY) == MARIA_TODAY


@pytest.mark.parametrize(
    ("typed", "kept_as"),
    [
        # A São Paulo landline: its 8 digits start with 2 to 5, no 9th digit.
        ("+55 11 3456-7890", "551134567890"),
        ("11 3456-7890", "551134567890"),
        # Other countries are left as typed (here Portugal, the US).
        ("+351 912 345 678", "351912345678"),
        ("+1 202 555 0123", "12025550123"),
    ],
)
def test_landlines_and_other_countries_are_left_alone(typed, kept_as):
    assert canonical_typed_phone(typed) == kept_as


def test_an_implausible_number_is_refused():
    assert canonical_typed_phone("1234") is None


# --- Intake: the 2026-11-07 case ---


def video_from(harness, phone, name="Maria (filmagem)", data=None):
    response, payload = send_video(
        harness, data or f"video of {phone}".encode(), sender_phone=phone, sender_name=name
    )
    assert response.status_code == 202, response.text
    return job_id_for_message(harness, payload["data"]["key"]["id"])


def contacts(harness):
    response = harness.client.get("/admin/contacts", auth=harness.admin_auth)
    return response.json()["contacts"]


def test_a_contact_typed_with_the_ninth_digit_gets_videos_from_a_jid_without_it(harness):
    # The admin adds Maria the way people write numbers today, with Enduro.
    prova = ensure_prova(harness, "Enduro")
    harness.client.post(
        "/admin/contacts",
        json={"phone": "+55 19 98123-4567", "default_prova_id": prova["id"]},
        auth=harness.admin_auth,
    )

    # Her phone's WhatsApp id has no 9th digit.
    job_id = video_from(harness, MARIA_JID)

    # The video gets Enduro, and she is still one contact (no "unknown" twin),
    # now with her WhatsApp name and the time she first wrote.
    assert harness.job(job_id)["prova"] == "Enduro"
    [maria] = contacts(harness)
    assert (maria["phone"], maria["status"], maria["default_prova"]) == (
        MARIA_TODAY,
        "allowed",
        "Enduro",
    )
    assert maria["first_seen_at"] is not None


def test_a_contact_typed_without_the_ninth_digit_gets_videos_from_a_jid_with_it(harness):
    # The reverse: typed the old way; the JID has the 9th digit.
    prova = ensure_prova(harness, "Suspensão")
    harness.client.post(
        "/admin/contacts",
        json={"phone": "+55 (19) 8123-4567", "default_prova_id": prova["id"]},
        auth=harness.admin_auth,
    )

    job_id = video_from(harness, MARIA_TODAY)

    assert harness.job(job_id)["prova"] == "Suspensão"
    assert [contact["phone"] for contact in contacts(harness)] == [MARIA_TODAY]


def test_a_number_typed_without_the_country_code_matches_too(harness):
    prova = ensure_prova(harness, "Tração")
    harness.client.post(
        "/admin/contacts",
        json={"phone": "19 98123-4567", "default_prova_id": prova["id"]},
        auth=harness.admin_auth,
    )

    job_id = video_from(harness, MARIA_JID)

    assert harness.job(job_id)["prova"] == "Tração"


def test_adding_a_number_that_already_wrote_in_the_other_form_is_one_contact(harness):
    # Maria wrote first (recorded from the JID, without the 9th digit); the
    # admin then types her number with it: it is the same contact, so the
    # admin edits that row instead of creating a second one.
    video_from(harness, MARIA_JID)

    response = harness.client.post(
        "/admin/contacts", json={"phone": "+55 19 98123-4567"}, auth=harness.admin_auth
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "already_exists"


def test_the_contact_can_be_edited_with_any_form_of_its_number(harness):
    video_from(harness, MARIA_JID)

    # A script using the JID's digits still finds her.
    response = harness.client.patch(
        f"/admin/contacts/{MARIA_JID}", json={"notes": "filma a Tração"}, auth=harness.admin_auth
    )

    assert response.status_code == 200, response.text
    assert response.json()["phone"] == MARIA_TODAY


def test_a_coringa_without_a_default_prova_keeps_leaving_it_empty(harness):
    # Pedro films everything: no default prova, so his videos come
    # with the prova empty for the reviewer to choose.
    harness.client.post(
        "/admin/contacts", json={"phone": "+55 11 98765-0000"}, auth=harness.admin_auth
    )

    job_id = video_from(harness, "551187650000", name="Pedro")

    assert harness.job(job_id)["prova"] is None


# --- A default prova set later: apply it to the videos still waiting ---


def set_default(harness, prova_id, apply):
    response = harness.client.patch(
        f"/admin/contacts/{MARIA_TODAY}",
        json={"default_prova_id": prova_id, "apply_default_prova_to_waiting": apply},
        auth=harness.admin_auth,
    )
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture
def maria_with_waiting_videos(harness):
    """Maria sent four videos before she had a default prova:

    * two still waiting, with no prova (one from each form of her number);
    * one waiting where a reviewer already typed Suspensão;
    * one already reviewed (ready), with Tração.
    """
    for name in ("Enduro", "Suspensão", "Tração"):
        ensure_prova(harness, name)
    master = harness.enroll(WorkerRole.MASTER, "master", tailnet_address="100.70.0.9")
    waiting = [
        video_from(harness, MARIA_JID, data=b"one"),
        video_from(harness, MARIA_TODAY, data=b"two"),
    ]
    chosen = video_from(harness, MARIA_JID, data=b"three")
    harness.client.patch(
        f"/jobs/{chosen}/review",
        json={"expected_version": 1, "action": "save", "prova": "Suspensão"},
        auth=harness.reviewer_auth,
    )
    reviewed = video_from(harness, MARIA_JID, data=b"four")
    hold_raw(harness, master, reviewed)
    make_proxy(harness, master, reviewed)
    review_to_ready(harness, reviewed, prova="Tração", car_number=7, passagem=1)
    return waiting, chosen, reviewed


def test_the_contact_says_how_many_waiting_videos_have_no_prova(harness, maria_with_waiting_videos):
    [maria] = contacts(harness)

    assert maria["waiting_without_prova"] == 2


def test_applying_a_new_default_fills_only_the_waiting_videos_without_a_prova(
    harness, maria_with_waiting_videos
):
    waiting, chosen, reviewed = maria_with_waiting_videos
    enduro = ensure_prova(harness, "Enduro")

    contact = set_default(harness, enduro["id"], apply=True)

    # Both waiting videos without a prova now say Enduro (whichever form of
    # her number they came from); the reviewer's choice and the reviewed
    # video are untouched.
    assert [harness.job(job_id)["prova"] for job_id in waiting] == ["Enduro", "Enduro"]
    assert harness.job(chosen)["prova"] == "Suspensão"
    assert harness.job(reviewed)["prova"] == "Tração"
    assert contact["waiting_without_prova"] == 0
    # Filling it is not a reviewer's change: an open job page can still save.
    assert harness.job(waiting[0])["version"] == 1
    # Each video's log says why its prova changed.
    events = harness.client.get(f"/jobs/{waiting[0]}/events", auth=harness.reviewer_auth).json()
    assert "default_prova_applied" in [event["kind"] for event in events["events"]]


def test_without_the_confirmation_the_waiting_videos_stay_as_they_are(
    harness, maria_with_waiting_videos
):
    waiting, _, _ = maria_with_waiting_videos
    enduro = ensure_prova(harness, "Enduro")

    contact = set_default(harness, enduro["id"], apply=False)

    assert [harness.job(job_id)["prova"] for job_id in waiting] == [None, None]
    assert contact["waiting_without_prova"] == 2
