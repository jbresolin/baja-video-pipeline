"""The admin Provas and Equipes pages, and the results API sync, through the review UI.

Against the real orchestrator (fixtures `harness`, `admin`, `reviewer` in
conftest.py). Its fake results API has the documented 26BR example: provas
SGE/END/EPN, teams 1 "BAJA UFMG" and 2 "Equipe FEI Baja" (short "FEI").
"""

from tests.review_ui.admin_browser import follow, post_form

# --- Provas ---


def test_provas_add_rename_move_and_deactivate(admin, harness):
    for name in ("Enduro", "Suspensão"):
        follow(admin, post_form(admin, "/review/admin/provas/create", name=name))
    provas = harness.client.get("/provas", headers=harness.admin_headers).json()["provas"]
    enduro, suspensao = provas

    page = follow(
        admin, post_form(admin, f"/review/admin/provas/{suspensao['id']}/move", direction="up")
    )
    assert "Ordem das provas salva." in page.text
    follow(admin, post_form(admin, f"/review/admin/provas/{enduro['id']}", name="Enduro 4h"))
    follow(admin, post_form(admin, f"/review/admin/provas/{enduro['id']}", active="0"))

    provas = harness.client.get("/provas", headers=harness.admin_headers).json()["provas"]
    assert [(p["name"], p["slug"], p["active"]) for p in provas] == [
        ("Suspensão", "suspensao", True),
        ("Enduro 4h", "enduro", False),  # renamed, same folder, deactivated
    ]
    # The page shows the fixed folder and the change in the latest changes.
    page = admin.get("/review/admin/provas").text
    assert "<code>enduro/</code>" in page
    assert "renamed &#39;Enduro&#39; → &#39;Enduro 4h&#39;" in page


def test_a_taken_prova_name_is_explained(admin):
    follow(admin, post_form(admin, "/review/admin/provas/create", name="Enduro"))
    response = post_form(admin, "/review/admin/provas/create", name="ENDURO")
    assert response.status_code == 409
    assert "Já existe." in response.text


# --- Equipes ---


def test_teams_add_edit_inline_delete(admin, harness):
    follow(
        admin,
        post_form(
            admin, "/review/admin/teams/save", car_number="42", team_name="Trovão", school="UFSC"
        ),
    )
    follow(
        admin,
        post_form(
            admin, "/review/admin/teams/save", car_number="42", team_name="Trovão Baja", school=""
        ),
    )
    team = harness.client.get("/teams/42", auth=harness.reviewer_auth).json()
    assert (team["team_name"], team["school"]) == ("Trovão Baja", None)
    page = follow(admin, post_form(admin, "/review/admin/teams/42/delete"))
    assert "Equipe removida" in page.text
    assert harness.client.get("/teams/42", auth=harness.reviewer_auth).status_code == 404


def set_event(admin, event_id="26BR"):
    """Save the event on the Settings page (that also syncs provas and teams)."""
    return post_form(
        admin,
        "/review/admin/settings",
        event_id=event_id,
        intake_mode="open",
        inbox_hold_gb="22",
        proxy_evict_min_free_gb="10",
        installer_download_url="",
    )


def test_setting_the_event_fills_provas_and_teams_from_the_results_site(admin):
    # The fake results API has the docs' 26BR example.
    page = follow(admin, set_event(admin))
    assert "Configurações salvas." in page.text
    assert 'value="26BR"' in page.text

    provas = admin.get("/review/admin/provas").text
    assert "Evento <code>26BR</code>" in provas
    assert "site <code>END</code>" in provas
    teams = admin.get("/review/admin/teams").text
    assert 'value="Equipe FEI Baja"' in teams and 'value="FEI"' in teams


def test_the_sync_button_reports_back_on_the_page_it_was_pressed(admin, harness):
    set_event(admin)
    harness.results_api.add_team(3, "Equipe Três", "Três")
    page = follow(admin, post_form(admin, "/review/admin/sync", back="teams"))
    assert page.url.path == "/review/admin/teams"
    assert "Sincronizado com o site de resultados." in page.text
    assert 'value="Equipe Três"' in page.text


def test_a_failed_sync_is_explained_on_the_page(admin, harness):
    set_event(admin)
    harness.results_api.rate_limited_for_s = 12
    page = post_form(admin, "/review/admin/sync", back="provas")
    assert page.status_code == 503
    assert "rate limit" in page.text


def test_correcting_a_synced_team_is_marked_and_can_be_undone(admin, harness):
    set_event(admin)
    follow(
        admin,
        post_form(
            admin,
            "/review/admin/teams/save",
            car_number="2",
            team_name="FEI",
            short_name="FEI Baja",
        ),
    )
    page = admin.get("/review/admin/teams").text
    assert "corrigida" in page and "Desfazer correção" in page
    follow(admin, post_form(admin, "/review/admin/teams/2/delete"))
    team = harness.client.get("/teams/2", auth=harness.reviewer_auth).json()
    assert team["team_name"] == "Equipe FEI Baja"


def test_without_an_event_the_pages_point_to_the_settings(admin):
    page = admin.get("/review/admin/provas").text
    assert "Nenhum evento configurado" in page
    assert 'href="/review/admin/settings"' in page
