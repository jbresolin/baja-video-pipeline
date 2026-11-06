"""Admin toggles change their row in place, without a page reload (LIVESTACK-42 item 11).

Background for a beginner: on Admin → Provas, every "Desativar"/"Reativar"
and every ↑/↓ used to post a form, and the browser reloaded the whole page
and jumped back to the top of a long list. The same on Admin → Contatos for
"Permitir"/"Bloquear". Now, with JavaScript on, those buttons call a small
JSON endpoint of the review UI (/review/api/admin/...), which makes the same
orchestrator change and answers with the changed rows already rendered as
HTML plus a short pt-BR message. The page script (static/js/admin.js) puts
the rows in place and shows the message beside the button. Without
JavaScript the forms still post normally.

These tests run against the real orchestrator (admin_browser.py) and check
the JSON endpoints, that the pages mark the right forms, and that the
script never reloads the page.
"""

from orchestrator.testing.scenarios import ensure_prova, send_video
from review_ui.templating import STATIC_DIR

JSON = {"Content-Type": "application/json"}


def provas_by_name(harness) -> dict[str, dict]:
    response = harness.client.get("/provas", auth=harness.reviewer_auth)
    return {prova["name"]: prova for prova in response.json()["provas"]}


# --- Provas ---


def test_the_provas_page_marks_its_toggles_for_in_place_changes(admin, harness):
    prova = ensure_prova(harness, "Abertura")

    html = admin.get("/review/admin/provas").text

    # The rows sit in one <tbody> the script can swap, and each toggle and
    # ↑/↓ form names the JSON endpoint it uses instead of reloading.
    assert '<tbody id="provas-rows">' in html
    assert f'data-inline="/review/api/admin/provas/{prova["id"]}/active"' in html
    assert f'data-inline="/review/api/admin/provas/{prova["id"]}/move"' in html


def test_deactivating_answers_with_the_new_rows_and_a_message(admin, harness):
    prova = ensure_prova(harness, "Abertura")

    response = admin.post(
        f"/review/api/admin/provas/{prova['id']}/active", json={"active": "0"}, headers=JSON
    )

    assert response.status_code == 200, response.text
    body = response.json()
    # The orchestrator really deactivated it...
    assert provas_by_name(harness)["Abertura"]["active"] is False
    # ...the answer is the table body as a reload would show it (the row is
    # muted and now offers "Reativar")...
    assert '<tbody id="provas-rows">' in body["html"]
    assert 'class="muted"' in body["html"]
    assert "Reativar" in body["html"]
    # ...and the message the script shows beside the button.
    assert body["message"] == "Prova desativada."


def test_reactivating_says_so(admin, harness):
    prova = ensure_prova(harness, "Abertura")
    admin.post(f"/review/api/admin/provas/{prova['id']}/active", json={"active": "0"}, headers=JSON)

    response = admin.post(
        f"/review/api/admin/provas/{prova['id']}/active", json={"active": "1"}, headers=JSON
    )

    assert response.json()["message"] == "Prova reativada."
    assert provas_by_name(harness)["Abertura"]["active"] is True


def test_moving_a_manual_prova_answers_with_the_rows_in_the_new_order(admin, harness):
    first = ensure_prova(harness, "Abertura")
    ensure_prova(harness, "Encerramento")

    response = admin.post(
        f"/review/api/admin/provas/{first['id']}/move", json={"direction": "down"}, headers=JSON
    )

    assert response.status_code == 200, response.text
    html = response.json()["html"]
    assert html.index('value="Encerramento"') < html.index('value="Abertura"')
    assert response.json()["message"] == "Ordem das provas salva."
    names = list(provas_by_name(harness))
    assert names.index("Encerramento") < names.index("Abertura")


def test_a_bad_direction_and_a_form_post_are_refused(admin, harness):
    prova = ensure_prova(harness, "Abertura")
    url = f"/review/api/admin/provas/{prova['id']}/move"

    # A direction that is neither up nor down: 400 with a pt-BR text.
    bad = admin.post(url, json={"direction": "sideways"}, headers=JSON)
    assert bad.status_code == 400
    assert bad.json()["text"]
    # A plain form post (what another site could send) is refused: JSON only.
    as_form = admin.post(url, data={"direction": "up"})
    assert as_form.status_code == 415


def test_a_reviewer_cannot_use_them(reviewer, harness):
    prova = ensure_prova(harness, "Abertura")

    response = reviewer.post(
        f"/review/api/admin/provas/{prova['id']}/active", json={"active": "0"}, headers=JSON
    )

    assert response.status_code == 403
    assert provas_by_name(harness)["Abertura"]["active"] is True


# --- Contatos ---


def test_blocking_a_contact_answers_with_its_row(admin, harness):
    send_video(harness, sender_phone="5511987654321")
    html = admin.get("/review/admin/contacts").text
    assert 'data-inline="/review/api/admin/contacts/5511987654321/status"' in html

    response = admin.post(
        "/review/api/admin/contacts/5511987654321/status", json={"status": "blocked"}, headers=JSON
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["message"] == "Contato bloqueado."
    # One row, now blocked, now offering "Permitir" (and no longer "Bloquear").
    assert body["html"].count("<tr>") == 1
    assert "badge contact-blocked" in body["html"]
    assert ">Permitir</button>" in body["html"]
    assert ">Bloquear</button>" not in body["html"]
    contacts = harness.client.get("/admin/contacts", auth=harness.admin_auth).json()["contacts"]
    assert contacts[0]["status"] == "blocked"


def test_a_contact_status_other_than_allow_or_block_is_refused(admin, harness):
    send_video(harness, sender_phone="5511987654321")

    response = admin.post(
        "/review/api/admin/contacts/5511987654321/status", json={"status": "unknown"}, headers=JSON
    )

    assert response.status_code == 400


# --- The page script ---


def test_the_admin_script_never_reloads_the_page():
    # The whole point: no full reload after an inline change. The script
    # swaps rows in; a call to location.reload() would undo that.
    script = (STATIC_DIR / "js" / "admin.js").read_text(encoding="utf-8")

    assert "location.reload" not in script
    assert "data-inline" in script
