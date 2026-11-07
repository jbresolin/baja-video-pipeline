"""Configurações → "Resultados das passagens" (LIVESTACK-47), against the REAL orchestrator.

Like the other admin page tests, the review UI runs on top of the real
orchestrator (conftest `harness` and `admin`). Its fake results API serves
the real 2026 Manobrabilidade table (tests/orchestrator/pass_results_data.py):
car 2's first run reads "58 s · 8 cones, 2 saídas de pista → 118 s".

* The block shows the configuration as saved and, per configured prova,
  our prova's name, its table and how fetching it goes.
* "Salvar" stores it and comes back with a done message; a bad text is
  refused with a precise pt-BR message, and the text typed stays in the box.
* "Testar" tries the text in the box (saved or not) and shows the line and
  the car's row, the site's HTML shown as text, never as HTML.

The pt-BR message for each problem code, without a server, is in
test_pass_results_settings_view.py.
"""

import re
from html import unescape

from orchestrator.domain.pass_results_fetch import poll_due_tables
from tests.orchestrator.pass_results_data import (
    MANOBRABILIDADE_CONFIG_TEXT,
    job_in_review,
    set_up_manobrabilidade,
)
from tests.review_ui.admin_browser import follow, post_form
from tests.review_ui.english_guard import english_words_in, visible_text

SETTINGS = "/review/admin/settings"
FORM = "/review/admin/pass-results"


def textarea_text(html: str) -> str:
    """What the configuration box holds, as the browser would show it."""
    match = re.search(r'<textarea id="config_text"[^>]*>(.*?)</textarea>', html, re.S)
    return unescape(match.group(1))


def block_of(html: str) -> str:
    """Only the "Resultados das passagens" section of the Settings page."""
    return html[html.index('id="pass-results"') : html.index('aria-label="Últimas alterações"')]


# --- The block as saved ---


def test_the_block_shows_the_saved_text_and_each_provas_status(admin, harness):
    # Setup: event 26BR synced (Manobrabilidade, API id "MAN") and the
    # configuration saved; a job in review, so the table is fetched.
    set_up_manobrabilidade(harness)
    job_in_review(harness)
    poll_due_tables(harness.context)

    html = admin.get(SETTINGS).text

    # The text exactly as saved, in a monospace box without spell checking.
    assert textarea_text(html) == MANOBRABILIDADE_CONFIG_TEXT
    assert 'rows="16" spellcheck="false"' in html
    # The prova: its id, our name for it, its table, fetched every 30 s, ok.
    block = block_of(html)
    assert "<code>MAN</code>" in block
    assert "Manobrabilidade" in block
    assert "<code>26BR_MAN</code>" in block
    assert "buscando a cada 30 s" in block
    assert re.search(r"última busca [\d/: ]+: ok", block)
    # "Testar" offers the configured prova.
    assert '<select name="test_prova">' in block
    # And the whole page stays pt-BR.
    assert not english_words_in(visible_text(html))


def test_a_failing_table_says_since_when_with_the_error_in_english(admin, harness):
    set_up_manobrabilidade(harness)
    job_in_review(harness)
    harness.results_api.unreachable = True
    poll_due_tables(harness.context)

    block = block_of(admin.get(SETTINGS).text)

    assert re.search(r'class="error-text">última busca [\d/: ]+: falhou', block)
    assert "inacessível desde" in block
    assert '<span lang="en" class="small">' in block


def test_a_prova_id_we_do_not_have_is_named_as_such(admin, harness):
    set_up_manobrabilidade(harness)
    unknown = MANOBRABILIDADE_CONFIG_TEXT.replace('"MAN": {', '"XYZ": {')
    response = post_form(admin, FORM, action="save", config_text=unknown)

    block = block_of(follow(admin, response).text)

    assert "<code>XYZ</code>" in block
    assert "nenhuma prova sincronizada com esse id" in block


# --- Salvar ---


def test_saving_stores_the_text_and_says_so(admin, harness):
    set_up_manobrabilidade(harness)
    # The browser sends the textarea's line ends as CRLF.
    typed = MANOBRABILIDADE_CONFIG_TEXT.replace('"decimals": 0', '"decimals": 1')
    response = post_form(admin, FORM, action="save", config_text=typed.replace("\n", "\r\n"))

    # Like every admin save: back to the page with a done message.
    assert response.headers["location"] == f"{SETTINGS}?feito=pass_results_saved"
    html = follow(admin, response).text
    assert "Configuração dos resultados das passagens salva." in html
    # Stored with plain line ends, and the change is in "Últimas alterações".
    saved = harness.client.get("/admin/pass-results", auth=harness.admin_auth).json()
    assert saved["config_text"] == typed
    assert "configuração dos resultados das passagens alterada" in html


def test_a_bad_text_is_refused_with_where_and_why_and_stays_in_the_box(admin, harness):
    set_up_manobrabilidade(harness)
    seven_decimals = MANOBRABILIDADE_CONFIG_TEXT.replace('"decimals": 0', '"decimals": 7')

    response = post_form(admin, FORM, action="save", config_text=seven_decimals)

    # Shown again (no redirect), with the place in <code> and the rule.
    assert response.status_code == 422
    assert (
        "Configuração não salva: em <code>provas.MAN.result.decimals</code>, "
        "o valor deve estar entre 0 e 3. A configuração anterior continua valendo."
    ) in response.text
    # The admin's text is kept, so they fix the 7 instead of typing it all again...
    assert textarea_text(response.text) == seven_decimals
    # ...and the old configuration is still the one in force.
    saved = harness.client.get("/admin/pass-results", auth=harness.admin_auth).json()
    assert saved["config_text"] == MANOBRABILIDADE_CONFIG_TEXT
    assert not english_words_in(visible_text(response.text))


def test_text_that_is_not_json_says_the_line_and_column(admin, harness):
    response = post_form(admin, FORM, action="save", config_text='{\n  "provas": {,}\n}')

    assert response.status_code == 422
    assert "Configuração não salva: o texto não é JSON válido (linha 2, coluna 14)." in (
        response.text
    )


# --- Testar ---


def test_testar_shows_the_line_and_the_cars_row_as_text(admin, harness):
    set_up_manobrabilidade(harness)

    response = post_form(
        admin,
        FORM,
        action="test",
        config_text=MANOBRABILIDADE_CONFIG_TEXT,
        test_prova="MAN",
        test_car="2",
        test_passagem="1",
    )

    assert response.status_code == 200, response.text
    html = response.text
    assert "Teste: prova MAN, carro 2, passagem 1" in html
    assert '<p class="pass-pill">58 s · 8 cones, 2 saídas de pista → 118 s</p>' in html
    assert "Tabela <code>26BR_MAN</code> baixada" in html
    # The car's row, column by column. The site's display is HTML
    # ("<img class='cone'>"): it must come out as text, never as an image.
    assert "<td>Tempo Corrigido</td>" in html
    assert "<img class='cone'>" not in html
    assert "8&lt;img class=&#39;cone&#39;&gt;" in html
    # The fields keep what was typed, ready for the next test.
    assert "<option selected>MAN</option>" in html
    assert 'name="test_car" type="number" min="0" max="9999" inputmode="numeric" value="2"' in html
    assert not english_words_in(visible_text(html))


def test_testar_tries_the_text_in_the_box_without_saving_it(admin, harness):
    set_up_manobrabilidade(harness)
    unsaved = MANOBRABILIDADE_CONFIG_TEXT.replace('"penalties": true', '"penalties": false')

    html = post_form(
        admin,
        FORM,
        action="test",
        config_text=unsaved,
        test_prova="MAN",
        test_car="2",
        test_passagem="1",
    ).text

    # The line follows the box (no penalties), and the page says it is not saved.
    assert '<p class="pass-pill">58 s → 118 s</p>' in html
    assert "O teste usou o texto da caixa, que ainda não foi salvo." in html
    assert textarea_text(html) == unsaved
    saved = harness.client.get("/admin/pass-results", auth=harness.admin_auth).json()
    assert saved["config_text"] == MANOBRABILIDADE_CONFIG_TEXT


def test_testar_says_why_there_is_no_line(admin, harness):
    set_up_manobrabilidade(harness)

    html = post_form(
        admin,
        FORM,
        action="test",
        config_text=MANOBRABILIDADE_CONFIG_TEXT,
        test_prova="MAN",
        test_car="2",
        test_passagem="5",
    ).text

    # Car 2 has two runs: the job page's sentence, with the English detail.
    assert "A tabela de resultados ainda não tem esta passagem deste carro." in html
    assert '<span lang="en" class="small">2 run(s) in the table</span>' in html


def test_testar_needs_the_three_fields(admin, harness):
    response = post_form(admin, FORM, action="test", config_text="", test_prova="MAN")

    assert response.status_code == 400
    assert "Para testar, preencha a prova" in response.text
