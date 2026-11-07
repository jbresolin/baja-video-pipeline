"""The welcome message on the admin pages (LIVESTACK-44), against the real orchestrator.

Background for a beginner: on Admin → Configurações João writes one
welcome text. On Admin → Contatos, adding a contact with the box "Enviar
mensagem de boas-vindas" ticked makes the bot send that text on WhatsApp;
each contact's row also has "Enviar boas-vindas" to send it (again).

`admin` is a browser signed in as the admin "joao"; `harness` is the real
orchestrator behind the pages, with a fake Evolution API whose `sent` list
holds every WhatsApp text the bot sent. Pages are plain HTML forms, so the
tests post forms like a browser does and read the HTML that comes back.
"""

import re

from tests.review_ui.admin_browser import follow, post_form

PHONE = "5511987654321"
CONTACTS = "/review/admin/contacts"
JSON = {"Content-Type": "application/json"}


def save_welcome_message(admin, text: str):
    """Post the whole Settings form with the stored values and a new message.

    The Settings form always sends every field, so the test reads the
    current values first, like the browser's form would hold them.
    """
    page = admin.get("/review/admin/settings").text
    fields = {
        name: value
        for name, value in re.findall(r'<input id="(\w+)" name="\w+"[^>]*?value="([^"]*)"', page)
    }
    fields.update(
        intake_mode="open",
        clip_team_name_style="short",
        clip_school_name_style="short",
        welcome_message=text,
    )
    return post_form(admin, "/review/admin/settings", **fields)


def add_contact(admin, **fields):
    return post_form(admin, f"{CONTACTS}/create", phone=PHONE, status="allowed", **fields)


def contact_row(html: str, phone: str = PHONE) -> str:
    """The <tr> of one contact on the Contacts page."""
    match = re.search(rf"<tr>\s*<td><code>{phone}</code>.*?</tr>", html, re.S)
    assert match, f"no row for {phone}"
    return match.group(0)


# --- Configurações ---


def test_the_settings_page_has_the_message_its_placeholders_and_a_preview(admin):
    html = admin.get("/review/admin/settings").text
    assert "Mensagem de boas-vindas para contatos novos" in html
    assert '<textarea id="welcome_message" name="welcome_message"' in html
    # The two placeholders are listed under the field with what they mean.
    assert "<code>{nome}</code>: o nome do contato" in html
    assert "<code>{prova}</code>: a prova padrão do contato" in html
    # The preview is the shipped message filled with the example name.
    preview = re.search(r'<div id="welcome-preview"[^>]*>(.*?)</div>', html, re.S).group(1)
    assert preview.startswith("Olá Maria Conceição! 👋 Este é o *Baja Bot*")


def test_saving_a_new_message_updates_the_preview(admin, harness):
    response = save_welcome_message(admin, "Oi {nome}, sua prova é {prova}. {equipe}")
    page = follow(admin, response)
    assert "Configurações salvas." in page.text
    assert harness.context.admin_settings.current.welcome_message == (
        "Oi {nome}, sua prova é {prova}. {equipe}"
    )
    # Example values filled in; the unknown {equipe} is shown as typed, so
    # João sees the typo before any contact does.
    assert "Oi Maria Conceição, sua prova é Enduro. {equipe}" in page.text


def test_an_empty_message_shows_that_nothing_will_be_sent(admin):
    page = follow(admin, save_welcome_message(admin, ""))
    assert "Mensagem vazia: nada é enviado." in page.text


# --- Contatos: the add form ---


def test_the_add_form_has_the_box_ticked_and_the_status_hook_for_the_script(admin):
    html = admin.get(CONTACTS).text
    # Ticked by default ("Permitido" is the first status), and marked so
    # js/admin.js can untick and disable it for "Bloqueado".
    assert re.search(
        r'<input type="checkbox" name="send_welcome" value="1"\s+checked data-welcome-checkbox>',
        html,
    )
    assert '<select name="status" data-welcome-status>' in html
    assert "Enviar mensagem de boas-vindas" in html


def test_adding_with_the_box_ticked_sends_the_message_and_says_so(admin, harness):
    response = add_contact(admin, display_name="Conceição Araújo", send_welcome="1")

    page = follow(admin, response)
    assert "Contato adicionado e mensagem de boas-vindas enviada." in page.text
    # One WhatsApp text, to the new contact, with the name filled in.
    [text] = harness.evolution.texts_to(PHONE)
    assert text.startswith("Olá Conceição Araújo! 👋")
    # The row says it was sent, and "Últimas alterações" lists the send.
    assert "enviada" in contact_row(page.text)
    assert "boas-vindas enviadas" in page.text


def test_adding_with_the_box_unticked_sends_nothing(admin, harness):
    # An unticked checkbox is simply missing from the form.
    page = follow(admin, add_contact(admin, display_name="Ana"))
    assert "Contato adicionado." in page.text
    assert harness.evolution.sent == []
    assert "nunca enviada" in contact_row(page.text)


def test_evolution_down_shows_the_contact_and_a_banner_with_the_reason(admin, harness):
    harness.evolution.down = True

    response = add_contact(admin, display_name="Ana", send_welcome="1")

    # No redirect: the page is shown at once with a warning banner, the
    # reason as Evolution's (English) words.
    assert response.status_code == 200
    banner = re.search(r'<div class="banner warning" role="alert"><p>(.*?)</p>', response.text)
    assert banner, response.text
    assert banner.group(1).startswith("Contato criado, mas a mensagem não foi enviada: ")
    assert '<span lang="en">connection refused' in banner.group(1)
    # The contact was saved anyway, and the failure is in the history.
    assert "nunca enviada" in contact_row(response.text)
    assert "boas-vindas não enviadas" in response.text


def test_a_blocked_contact_gets_nothing_even_if_the_box_came_ticked(admin, harness):
    # Without JavaScript the box stays ticked when "Bloqueado" is chosen;
    # the orchestrator still sends nothing and the page says why.
    response = post_form(
        admin, f"{CONTACTS}/create", phone=PHONE, status="blocked", send_welcome="1"
    )
    assert harness.evolution.sent == []
    assert "Contato criado, mas a mensagem não foi enviada" in response.text
    assert "the contact is blocked" in response.text


# --- Contatos: each row ---


def test_each_row_can_send_it_again_after_a_confirmation(admin, harness):
    follow(admin, add_contact(admin, display_name="Ana", send_welcome="1"))
    row = contact_row(admin.get(CONTACTS).text)
    # The form asks first (form[data-confirm] in js/admin.js).
    assert f'action="/review/admin/contacts/{PHONE}/welcome"' in row
    assert 'data-confirm="Enviar a mensagem de boas-vindas para Ana agora?"' in row

    page = follow(admin, post_form(admin, f"{CONTACTS}/{PHONE}/welcome", send="1"))

    assert "Mensagem de boas-vindas enviada." in page.text
    assert len(harness.evolution.texts_to(PHONE)) == 2


def test_sending_again_while_evolution_is_down_shows_the_reason(admin, harness):
    follow(admin, add_contact(admin))
    harness.evolution.refuse_texts_with = 500
    response = post_form(admin, f"{CONTACTS}/{PHONE}/welcome", send="1")
    assert response.status_code == 200
    assert "A mensagem de boas-vindas não foi enviada: " in response.text
    assert '<span lang="en">HTTP 500' in response.text


def test_a_blocked_contacts_button_is_disabled(admin):
    follow(admin, add_contact(admin))
    follow(admin, post_form(admin, f"{CONTACTS}/{PHONE}", status="blocked"))
    row = contact_row(admin.get(CONTACTS).text)
    assert 'disabled title="Contatos bloqueados não recebem boas-vindas."' in row


def test_blocking_in_place_answers_with_a_row_whose_button_is_disabled(admin):
    # "Bloquear" with JavaScript on swaps the row without a reload; the new
    # row must disable the welcome button too.
    follow(admin, add_contact(admin))
    response = admin.post(
        f"/review/api/admin/contacts/{PHONE}/status", json={"status": "blocked"}, headers=JSON
    )
    assert response.status_code == 200, response.text
    assert "Contatos bloqueados não recebem boas-vindas." in response.json()["html"]


# --- An empty message disables both controls ---


def test_an_empty_message_disables_the_box_and_the_buttons_and_points_to_settings(admin, harness):
    follow(admin, add_contact(admin))
    follow(admin, save_welcome_message(admin, ""))

    html = admin.get(CONTACTS).text

    assert '<input type="checkbox" name="send_welcome" value="1"\n    disabled>' in html
    assert 'href="/review/admin/settings">Configurações</a>' in html
    assert "A mensagem de boas-vindas está vazia, então não pode ser enviada." in html
    row = contact_row(html)
    assert 'disabled title="A mensagem de boas-vindas está vazia (Configurações)."' in row
    # And the orchestrator refuses a post that gets through anyway (409).
    response = post_form(admin, f"{CONTACTS}/{PHONE}/welcome", send="1")
    assert response.status_code == 409
    assert harness.evolution.sent == []
