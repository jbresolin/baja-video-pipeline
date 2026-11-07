"""The welcome message the bot sends to a contact the admin adds (LIVESTACK-44).

When João adds a filmmaker on Admin → Contatos, the bot tells them, on
WhatsApp, which number it is and how to send videos. The text is a
setting (Admin → Configurações) with two placeholders, {nome} and
{prova}, filled in per contact.

These tests call the orchestrator's API the way the review UI does and
look at what the fake Evolution API received (`harness.evolution.sent`):
that list is exactly what would have gone out on WhatsApp. Turning the
fake "down", or making it refuse texts, shows what happens when the real
Evolution fails.
"""

from orchestrator.domain.welcome import CONTACT_IS_BLOCKED, MESSAGE_IS_EMPTY
from orchestrator.testing.scenarios import ensure_prova, send_video
from shared.welcome_message import DEFAULT_WELCOME_MESSAGE

PHONE = "5511987654321"
# What the admin types into the "Número" field: punctuation is dropped.
PHONE_AS_TYPED = "+55 (11) 98765-4321"


def set_welcome_message(harness, text: str) -> None:
    response = harness.client.patch(
        "/admin/settings", json={"welcome_message": text}, auth=harness.admin_auth
    )
    assert response.status_code == 200, response.text


def add_contact(harness, **fields):
    """POST /admin/contacts as the admin "joao"; returns the raw response."""
    body = {"phone": PHONE_AS_TYPED, **fields}
    return harness.client.post("/admin/contacts", json=body, auth=harness.admin_auth)


def send_again(harness, phone: str = PHONE):
    return harness.client.post(f"/admin/contacts/{phone}/welcome", auth=harness.admin_auth)


def the_contact(harness, phone: str = PHONE) -> dict:
    listed = harness.client.get("/admin/contacts", auth=harness.admin_auth).json()["contacts"]
    [contact] = [contact for contact in listed if contact["phone"] == phone]
    return contact


def welcome_events(harness) -> list[dict]:
    """The welcome_sent / welcome_failed events, oldest first."""
    events = harness.client.get("/admin/audit?area=contacts", auth=harness.admin_auth).json()[
        "events"
    ]
    return [e for e in reversed(events) if e["kind"] in ("welcome_sent", "welcome_failed")]


# --- The message on the Settings page ---


def test_a_new_server_starts_with_the_default_portuguese_message(harness):
    # Nobody has edited it yet, so the Settings page shows the shipped text.
    settings = harness.client.get("/admin/settings", auth=harness.admin_auth).json()["settings"]
    assert settings["welcome_message"] == DEFAULT_WELCOME_MESSAGE
    # It says what the brief asks it to say: who the bot is, where the
    # videos go, send as a document, one video per pass, save the number.
    for words in (
        "Baja Bot",
        "Baja SAE Brasil",
        "transmissão ao vivo",
        "*documento*",
        "qualidade",
        "Um vídeo por passagem",
        "Salve este número",
    ):
        assert words in DEFAULT_WELCOME_MESSAGE


def test_the_message_is_stored_with_plain_line_ends_and_without_outer_blank_lines(harness):
    # A browser's text area sends "\r\n" line ends; WhatsApp wants "\n".
    set_welcome_message(harness, "\r\nOlá {nome}!\r\n\r\nSalve este número.\r\n  ")
    settings = harness.client.get("/admin/settings", auth=harness.admin_auth).json()["settings"]
    assert settings["welcome_message"] == "Olá {nome}!\n\nSalve este número."


def test_a_message_longer_than_2000_characters_is_refused(harness):
    response = harness.client.patch(
        "/admin/settings", json={"welcome_message": "a" * 2001}, auth=harness.admin_auth
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_error"


def test_editing_the_message_is_an_audited_settings_change(harness):
    set_welcome_message(harness, "Oi {nome}")
    [event] = harness.client.get("/admin/audit?area=settings", auth=harness.admin_auth).json()[
        "events"
    ]
    assert event["kind"] == "settings_updated"
    assert event["details"]["after"] == {"welcome_message": "Oi {nome}"}


# --- Sent when the contact is added ---


def test_adding_a_contact_with_the_box_ticked_sends_one_text_with_the_filled_message(harness):
    # A real-looking contact: a name with accents and a default prova.
    set_welcome_message(harness, "Olá {nome}! Sua prova: {prova}. Salve este número.")
    enduro = ensure_prova(harness, "Enduro de Resistência")

    response = add_contact(
        harness,
        display_name="Conceição Araújo",
        default_prova_id=enduro["id"],
        send_welcome=True,
    )

    assert response.status_code == 201, response.text
    created = response.json()
    assert created["welcome"] == {"sent": True, "error": None}
    # Exactly one WhatsApp text, to the digits of the number the admin
    # typed, with both placeholders filled in. Not a quoted reply: there
    # is no message of theirs to answer.
    [sent] = harness.evolution.sent
    assert sent.number == PHONE
    assert sent.text == "Olá Conceição Araújo! Sua prova: Enduro de Resistência. Salve este número."
    assert sent.quoted is None
    # The row on the Contacts page can now say when it was sent.
    assert the_contact(harness)["welcome_sent_at"] == created["welcome_sent_at"] is not None


def test_the_send_is_recorded_as_an_event_of_the_admin_who_added_the_contact(harness):
    add_contact(harness, display_name="Ana", send_welcome=True)
    [event] = welcome_events(harness)
    assert (event["kind"], event["actor"]) == ("welcome_sent", "admin:joao")
    assert event["details"]["after"]["phone"] == PHONE
    # The event keeps the text as it went out (name filled in).
    assert event["details"]["after"]["text"].startswith("Olá Ana! 👋")


def test_a_contact_with_no_name_and_no_default_prova_gets_the_text_without_them(harness):
    # {nome} and {prova} have nothing to put in: they disappear, and so
    # does the space before each, so the greeting reads "Olá!".
    set_welcome_message(harness, "Olá {nome}! Prova padrão:{prova} nenhuma.")
    add_contact(harness, send_welcome=True)
    assert harness.evolution.texts_to(PHONE) == ["Olá! Prova padrão: nenhuma."]


def test_a_stray_brace_or_an_unknown_placeholder_is_sent_as_typed(harness):
    # Typos must not crash the send or eat text: only {nome} and {prova}
    # are placeholders.
    set_welcome_message(harness, "Oi {Nome} {equipe} {nome} :-{ 50% {")
    response = add_contact(harness, display_name="Bia", send_welcome=True)
    assert response.status_code == 201
    assert harness.evolution.texts_to(PHONE) == ["Oi {Nome} {equipe} Bia :-{ 50% {"]


def test_without_the_box_nothing_is_sent(harness):
    # Scripts that add contacts (and older pages) send no `send_welcome`.
    response = add_contact(harness, display_name="Ana")
    assert response.status_code == 201
    assert response.json()["welcome"] is None
    assert harness.evolution.sent == []
    assert welcome_events(harness) == []


def test_a_blocked_contact_is_never_sent_the_message(harness):
    # Even if the request asks for it (the page disables the box for
    # "bloqueado", but an old page or a script might not).
    response = add_contact(harness, status="blocked", send_welcome=True)
    assert response.status_code == 201
    assert response.json()["welcome"] == {"sent": False, "error": CONTACT_IS_BLOCKED}
    assert harness.evolution.sent == []
    # Not tried, so there is no welcome_failed event either.
    assert welcome_events(harness) == []


# --- Evolution failing ---


def test_evolution_down_still_creates_the_contact_and_says_why_nothing_was_sent(harness):
    harness.evolution.down = True

    response = add_contact(harness, display_name="Ana", send_welcome=True)

    # The contact exists: a failed message never undoes it.
    assert response.status_code == 201, response.text
    assert the_contact(harness)["display_name"] == "Ana"
    # The answer carries the reason, for the page's banner.
    welcome = response.json()["welcome"]
    assert welcome["sent"] is False
    assert "connection refused" in welcome["error"]
    # It is not marked as sent, and the failure is an event with the reason.
    assert the_contact(harness)["welcome_sent_at"] is None
    [event] = welcome_events(harness)
    assert (event["kind"], event["actor"]) == ("welcome_failed", "admin:joao")
    assert "connection refused" in event["reason"]


def test_evolution_refusing_the_text_reports_its_own_answer(harness):
    # Evolution is up but answers 500 (e.g. the WhatsApp session dropped).
    harness.evolution.refuse_texts_with = 500
    welcome = add_contact(harness, send_welcome=True).json()["welcome"]
    assert welcome["sent"] is False
    assert welcome["error"].startswith("HTTP 500")
    assert "Connection Closed" in welcome["error"]


# --- "Enviar boas-vindas" on a contact's row ---


def test_the_message_can_be_sent_again_later_with_the_edited_text(harness):
    add_contact(harness, display_name="Ana", send_welcome=True)
    first_sent_at = the_contact(harness)["welcome_sent_at"]
    # João edits the message an hour later and sends it again.
    harness.clock.advance(3600)
    set_welcome_message(harness, "Oi {nome}, mudamos o horário.")

    response = send_again(harness)

    assert response.status_code == 200, response.text
    answer = response.json()
    assert answer["welcome"] == {"sent": True, "error": None}
    # Two texts in all; the second is the new wording.
    assert harness.evolution.texts_to(PHONE)[1] == "Oi Ana, mudamos o horário."
    # "Last sent" moved on, in the answer and on the list.
    assert answer["contact"]["welcome_sent_at"] > first_sent_at
    assert the_contact(harness)["welcome_sent_at"] == answer["contact"]["welcome_sent_at"]
    assert [e["kind"] for e in welcome_events(harness)] == ["welcome_sent", "welcome_sent"]


def test_a_sender_who_wrote_first_can_be_welcomed_from_their_row(harness):
    # Contacts recorded by intake (status "unknown", name from WhatsApp)
    # are not blocked, so the button works for them too.
    send_video(harness, sender_phone=PHONE)
    assert send_again(harness).json()["welcome"]["sent"] is True
    assert len(harness.evolution.texts_to(PHONE)) == 1


def test_sending_again_while_evolution_is_down_answers_200_with_the_reason(harness):
    add_contact(harness)
    harness.evolution.down = True
    response = send_again(harness)
    # Not an HTTP error: the request was carried out, the message failed.
    assert response.status_code == 200
    assert response.json()["welcome"]["sent"] is False
    assert [e["kind"] for e in welcome_events(harness)] == ["welcome_failed"]


def test_sending_again_to_a_blocked_contact_is_refused(harness):
    add_contact(harness, status="blocked")
    response = send_again(harness)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "conflict"
    assert harness.evolution.sent == []


def test_sending_to_a_contact_that_does_not_exist_is_404(harness):
    assert send_again(harness, "5511900000000").status_code == 404


def test_a_reviewer_cannot_send_it(harness):
    add_contact(harness)
    response = harness.client.post(f"/admin/contacts/{PHONE}/welcome", auth=harness.reviewer_auth)
    assert response.status_code == 403
    assert harness.evolution.sent == []


# --- An empty message turns the feature off ---


def test_an_empty_message_sends_nothing_and_the_contacts_list_says_so(harness):
    set_welcome_message(harness, "   ")
    # The Contacts page reads this flag to disable the box and the button.
    listing = harness.client.get("/admin/contacts", auth=harness.admin_auth).json()
    assert listing["welcome_message_set"] is False

    created = add_contact(harness, send_welcome=True)
    assert created.status_code == 201
    assert created.json()["welcome"] == {"sent": False, "error": MESSAGE_IS_EMPTY}
    assert send_again(harness).status_code == 409
    assert harness.evolution.sent == []


def test_with_a_message_the_contacts_list_says_it_can_be_sent(harness):
    listing = harness.client.get("/admin/contacts", auth=harness.admin_auth).json()
    assert listing["welcome_message_set"] is True
