"""Every admin page and action, through the review UI, against the real orchestrator.

The admin pages live under /review/admin/ (Usuários, Contatos, Provas,
Equipes, Cadastro de PCs, Workers, Configurações). They are plain HTML
forms: a successful change redirects back to the page with ?feito=<key>
(the page then says what was done); a refused change shows the page again
with the reason.

Two users exist in every test (imported from the harness's reviewers.txt):
"joao" is an admin, "ana" a reviewer.
"""

import pytest

from orchestrator.testing.scenarios import ensure_prova, job_id_for_message, send_video
from shared.models import WorkerRole
from shared.testing.credentials import (
    PLACEHOLDER_ADMIN_TOKEN,
    PLACEHOLDER_OTHER_PASSWORD,
    PLACEHOLDER_SHORT_PASSWORD,
    THIRD_USERNAME,
)
from tests.review_ui.admin_browser import follow, post_form

ADMIN_PAGES = ["users", "contacts", "provas", "teams", "enrollment", "workers", "settings"]


# --- Who sees what ---


@pytest.mark.parametrize("page", ADMIN_PAGES)
def test_every_admin_page_opens_for_an_admin_in_portuguese(admin, page):
    response = admin.get(f"/review/admin/{page}")
    assert response.status_code == 200
    assert '<html lang="pt-BR">' in response.text
    assert "Últimas alterações" in response.text


@pytest.mark.parametrize("page", ADMIN_PAGES)
def test_a_reviewer_gets_403_on_every_admin_page(reviewer, page):
    # The UI does not decide this; the orchestrator answers 403 and the UI
    # shows its "Not allowed" page.
    response = reviewer.get(f"/review/admin/{page}")
    assert response.status_code == 403
    assert "Not allowed" in response.text


def test_the_admin_link_is_shown_to_admins_only(admin, reviewer):
    assert 'href="/review/admin/"' in admin.get("/review/").text
    assert 'href="/review/admin/"' not in reviewer.get("/review/").text


def test_a_reviewers_form_post_to_an_admin_action_is_refused_too(reviewer, harness):
    response = post_form(reviewer, "/review/admin/provas/create", name="Enduro")
    assert response.status_code == 403
    assert harness.client.get("/provas", headers=harness.admin_headers).json()["provas"] == []


def test_admin_forms_posted_from_another_site_are_refused(admin):
    response = admin.post(
        "/review/admin/provas/create",
        data={"name": "Enduro"},
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": "https://evil.example",
        },
    )
    assert response.status_code == 403


# --- Usuários ---


def test_users_add_disable_and_refuse_a_duplicate(admin, harness):
    page = follow(
        admin,
        post_form(
            admin,
            "/review/admin/users/create",
            username="bruno",
            password=PLACEHOLDER_OTHER_PASSWORD,
            role="reviewer",
        ),
    )
    assert "Usuário criado." in page.text
    assert "<strong>bruno</strong>" in page.text

    page = follow(
        admin, post_form(admin, "/review/admin/users/bruno", role="reviewer", disabled="1")
    )
    assert "Usuário atualizado." in page.text
    users = harness.client.get("/admin/users", headers=harness.admin_headers).json()["users"]
    assert next(u for u in users if u["username"] == "bruno")["disabled"] is True

    duplicate = post_form(
        admin,
        "/review/admin/users/create",
        username="bruno",
        password=PLACEHOLDER_OTHER_PASSWORD,
        role="reviewer",
    )
    assert duplicate.status_code == 409
    assert "Já existe." in duplicate.text


def test_a_too_short_password_is_explained(admin):
    response = post_form(
        admin,
        "/review/admin/users/create",
        username=THIRD_USERNAME,
        password=PLACEHOLDER_SHORT_PASSWORD,
    )
    assert response.status_code == 400
    assert "Senha: de 8 a 72 caracteres" in response.text


# --- Contatos ---


def test_a_new_sender_appears_and_one_click_allows_them(admin, harness):
    send_video(harness, sender_phone="5511987654321")
    page = admin.get("/review/admin/contacts")
    assert "5511987654321" in page.text
    assert "Desconhecido" in page.text

    page = follow(admin, post_form(admin, "/review/admin/contacts/5511987654321", status="allowed"))
    assert "Contato atualizado." in page.text
    contacts = harness.client.get("/admin/contacts", headers=harness.admin_headers).json()
    assert contacts["contacts"][0]["status"] == "allowed"


def test_the_contact_row_form_sets_name_default_prova_and_notes(admin, harness):
    prova = ensure_prova(harness, "Enduro")
    send_video(harness, sender_phone="5511987654321")
    follow(
        admin,
        post_form(
            admin,
            "/review/admin/contacts/5511987654321",
            display_name="Maria",
            default_prova_id=str(prova["id"]),
            notes="equipe 42",
        ),
    )
    [contact] = harness.client.get("/admin/contacts", headers=harness.admin_headers).json()[
        "contacts"
    ]
    assert (contact["display_name"], contact["default_prova"], contact["notes"]) == (
        "Maria",
        "Enduro",
        "equipe 42",
    )


def test_a_contact_can_be_added_before_they_write_and_the_list_can_be_filtered(admin):
    page = follow(
        admin,
        post_form(
            admin, "/review/admin/contacts/create", phone="+55 11 90000-1111", status="allowed"
        ),
    )
    assert "Contato adicionado." in page.text
    assert "<code>5511900001111</code>" in page.text
    # Filtered to blocked contacts, the row is not in the table.
    blocked_only = admin.get("/review/admin/contacts?status=blocked").text
    assert "<code>5511900001111</code>" not in blocked_only


# --- Cadastro de PCs ---


def test_a_new_token_is_shown_once_with_a_copy_button_and_the_installer_link(admin, harness):
    response = post_form(
        admin,
        "/review/admin/enrollment/create",
        volunteer_name="Maria",
        role="worker",
        valid_for_hours="72",
    )
    assert response.status_code == 200
    # Never cached: the token is in this page only.
    assert response.headers["cache-control"] == "no-store"
    assert 'data-copy-from="new-token"' in response.text
    assert "releases/latest" in response.text
    token = response.text.split('<code id="new-token">')[1].split("</code>")[0]
    # The token really works at /enroll...
    enrolled = harness.client.post(
        "/enroll",
        json={
            "enrollment_token": token,
            "worker_name": "pc",
            "capabilities": {"has_nvenc": False, "video_encoder": "libx264"},
            "storage_cap_bytes": 1,
        },
    )
    assert enrolled.status_code == 201
    # ...and is never shown again: the list has only its short id.
    listing = admin.get("/review/admin/enrollment").text
    assert token not in listing
    assert "Usado" in listing


def test_an_unused_token_can_be_revoked(admin, harness):
    post_form(
        admin,
        "/review/admin/enrollment/create",
        volunteer_name="Maria",
        role="worker",
        valid_for_hours="24",
    )
    [token] = harness.client.get("/admin/enrollment-tokens", headers=harness.admin_headers).json()[
        "tokens"
    ]
    page = follow(admin, post_form(admin, f"/review/admin/enrollment/{token['token_id']}/revoke"))
    assert "Token revogado." in page.text
    assert "Revogado" in page.text


# --- Workers ---


def test_revoking_a_worker_shows_the_tailnet_instruction(admin, harness):
    worker = harness.enroll(WorkerRole.WORKER, "pc-da-maria")
    page = admin.get("/review/admin/workers").text
    assert "pc-da-maria" in page
    assert "login.tailscale.com/admin/machines" in page

    page = follow(admin, post_form(admin, f"/review/admin/workers/{worker.worker_id}/revoke"))
    assert "Worker revogado." in page.text
    assert "Remova o PC do tailnet" in page.text
    heartbeat = harness.client.post(
        f"/workers/{worker.worker_id}/heartbeat",
        json={"free_bytes": 1, "storage_cap_bytes": 1},
        headers=worker.headers,
    )
    assert heartbeat.status_code == 401


# --- Configurações ---


def test_settings_save_and_take_effect_on_intake(admin, harness):
    page = follow(
        admin,
        post_form(
            admin,
            "/review/admin/settings",
            intake_mode="allowlist",
            inbox_hold_gb="20,5",  # a Brazilian decimal comma is understood
            proxy_evict_min_free_gb="8",
            installer_download_url="https://example.org/BajaWorker-Setup.exe",
        ),
    )
    assert "Configurações salvas." in page.text
    assert 'value="20.5"' in page.text
    # Allowlist mode is live: an unknown sender's video makes no job.
    response, payload = send_video(harness, sender_phone="5511912345678")
    assert response.json()["ignored"] is True
    assert job_id_for_message(harness, payload["data"]["key"]["id"]) is None


def test_settings_status_shows_secrets_as_configured_but_never_their_values(admin):
    page = admin.get("/review/admin/settings").text
    assert "<code>BVP_ADMIN_TOKEN</code>: configurado" in page
    assert PLACEHOLDER_ADMIN_TOKEN not in page
    assert "Conectado" in page  # the fake Evolution says "open"


def test_an_inbox_soft_cap_above_the_hard_cap_is_explained(admin):
    response = post_form(
        admin,
        "/review/admin/settings",
        intake_mode="open",
        inbox_hold_gb="30",
        proxy_evict_min_free_gb="10",
        installer_download_url="",
    )
    assert response.status_code == 400
    assert "BVP_INBOX_CAP_GB" in response.text
