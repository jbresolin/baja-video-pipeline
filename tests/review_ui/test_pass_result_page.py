"""The review UI's side of LIVESTACK-47 on the job page: the official result of the passagem.

* For a prova in the pass-results configuration, the job page has a
  "Mostrar resultado da passagem" box under "Mostrar o número da passagem",
  ticked as the orchestrator says (`Job.pass_result.show`), and one line
  saying what will be burned in, or why there is nothing yet.
* When the results API is failing, the line says since when, and when the
  last good answer was fetched.
* static/js/review_form.js sends `show_pass_result` only when the box is on
  the page: test_pass_result_toggle_js.py.
* A clip whose result changed after delivery says so in words.
* The orchestrator's new waiting sentence and Frota's feature are in pt-BR.

Setup (conftest.py): `ui` is a signed-in browser against a fake
orchestrator holding a few jobs. sample_data.pass_result_job() is car 26's
second run in Manobrabilidade, result "66 s · 4 cones → 86 s".
"""

from datetime import UTC, datetime

import pytest

from orchestrator.domain.render_features import NO_HOLDER_CAN_RENDER_PASS_RESULT
from review_ui.clip_inputs_view import difference_sentence
from review_ui.clips_and_fleet_view import render_features_text
from review_ui.job_view import NO_HOLDER_CAN_RENDER_PASS_RESULT_REASON, describe_waiting_reason
from review_ui.pass_result_view import pass_result_box
from shared.models import PassResult, PassResultProblem, RenderInputField, RenderInputs
from tests.review_ui.english_guard import english_words_in, visible_text
from tests.review_ui.sample_data import (
    T0,
    failing_fetch,
    make_fleet,
    make_job,
    pass_result,
    pass_result_job,
)

# 14:30 in São Paulo on the day of the failing_fetch() sample (UTC-3).
LATER_THAT_DAY = datetime(2026, 10, 25, 17, 30, tzinfo=UTC)


# --- What the line says (pass_result_view.py, no server) ---


def test_a_shown_result_says_what_goes_on_the_overlay():
    box = pass_result_box(pass_result(), T0)

    assert box.show is True
    assert box.status == "Na tarja: 66 s · 4 cones → 86 s"
    # Fetching works, so there is no warning.
    assert (box.detail, box.fetch_warning, box.fetch_error) == (None, None, None)


def test_a_result_turned_off_says_it_will_not_be_burned_in():
    box = pass_result_box(pass_result(show=False), T0)

    assert box.show is False
    assert box.status == "Não será gravado (resultado: 66 s · 4 cones → 86 s)."


def test_no_box_at_all_for_a_prova_without_results_or_an_older_orchestrator():
    # Not configured: no toggle is offered. None: an orchestrator before 47.
    assert pass_result_box(PassResult(problem=PassResultProblem.NOT_CONFIGURED), T0) is None
    assert pass_result_box(None, T0) is None


@pytest.mark.parametrize(
    ("problem", "detail", "sentence"),
    [
        ("not_configured", None, "Esta prova não está na configuração dos resultados"),
        ("no_event", None, "Defina o evento nas Configurações."),
        ("incomplete_review", None, "Salve o número do carro e a passagem"),
        ("not_fetched", None, "A tabela de resultados desta prova ainda não foi baixada."),
        ("column_missing", "no column 'Tempo'", "A coluna configurada não está na tabela"),
        ("car_not_in_table", "car 26", "Este carro não está na tabela de resultados"),
        ("no_such_run", "1 run(s) in the table", "ainda não tem esta passagem deste carro"),
        ("no_result_yet", "'-'", "Ainda sem resultado oficial para esta passagem."),
        ("not_a_number", "'DNF'", "não é um número, então não vai na tarja"),
    ],
)
def test_every_reason_for_no_result_has_a_pt_br_sentence(problem, detail, sentence):
    # Setup: a configured job with no text, for each reason the orchestrator gives.
    result = pass_result(text=None, problem=problem, problem_detail=detail)

    box = pass_result_box(result, T0)

    # The sentence is pt-BR; the orchestrator's details (English) are kept
    # apart, so the page can show them marked lang="en".
    assert sentence in box.status
    assert not english_words_in(box.status)
    assert box.detail == detail


def test_every_problem_value_is_covered_above():
    # A new PassResultProblem in shared/ must get a sentence (and a test row).
    assert {problem.value for problem in PassResultProblem} == {
        "not_configured",
        "no_event",
        "incomplete_review",
        "not_fetched",
        "column_missing",
        "car_not_in_table",
        "no_such_run",
        "no_result_yet",
        "not_a_number",
    }


def test_a_failing_results_api_says_since_when_and_the_last_good_answer():
    # Setup: down since 14:02 São Paulo time, last good answer at 13:58.
    result = pass_result(fetch=failing_fetch())

    box = pass_result_box(result, LATER_THAT_DAY)

    # The text from the last good answer is still what goes on air...
    assert box.status == "Na tarja: 66 s · 4 cones → 86 s"
    # ...and the line says the API is down, in São Paulo time.
    assert box.fetch_warning == (
        "API de resultados inacessível desde 14:02 (último resultado de 13:58)."
    )
    assert box.fetch_error == "results API unreachable: connection refused"


def test_a_results_api_that_never_answered_says_so():
    fetch = failing_fetch(last_success_at=None, stale=False)
    result = pass_result(text=None, problem="not_fetched", fetch=fetch)

    box = pass_result_box(result, LATER_THAT_DAY)

    assert box.fetch_warning == (
        "API de resultados inacessível desde 14:02; nenhum resultado baixado ainda."
    )


# --- The job page (fake orchestrator) ---


def test_the_job_page_offers_the_toggle_ticked_with_the_line(ui, fake_orchestrator):
    fake_orchestrator.add_job(pass_result_job("job-result"))

    html = ui.get("/review/jobs/job-result").text

    assert 'name="show_pass_result" checked' in html
    assert "Mostrar resultado da passagem" in html
    assert "Na tarja: 66 s · 4 cones → 86 s" in html


def test_the_toggle_is_unticked_when_the_result_is_not_shown(ui, fake_orchestrator):
    fake_orchestrator.add_job(pass_result_job("job-off", pass_result=pass_result(show=False)))

    html = ui.get("/review/jobs/job-off").text

    assert 'name="show_pass_result" >' in html
    assert "Não será gravado (resultado: 66 s · 4 cones → 86 s)." in html


def test_no_toggle_for_a_prova_without_results(ui, fake_orchestrator):
    # The sample job has no pass_result (an older orchestrator); this one's
    # prova is simply not configured.
    not_configured = PassResult(problem=PassResultProblem.NOT_CONFIGURED)
    fake_orchestrator.add_job(make_job("job-plain", pass_result=not_configured))

    for job_id in ("job-plain", "job-awaiting-1"):
        assert "show_pass_result" not in ui.get(f"/review/jobs/{job_id}").text


def test_the_api_down_and_its_english_error_are_on_the_page(ui, fake_orchestrator):
    job = pass_result_job("job-down", pass_result=pass_result(fetch=failing_fetch()))
    fake_orchestrator.add_job(job)

    html = ui.get("/review/jobs/job-down").text

    assert "API de resultados inacessível desde" in html
    # The orchestrator's English error is marked as English for screen readers.
    assert '<span lang="en" class="small">results API unreachable' in html
    # And the page stays pt-BR outside lang="en".
    assert not english_words_in(visible_text(html))


def test_a_problem_detail_is_shown_marked_as_english(ui, fake_orchestrator):
    result = pass_result(text=None, problem="not_a_number", problem_detail="'DNF'")
    fake_orchestrator.add_job(pass_result_job("job-dnf", pass_result=result))

    html = ui.get("/review/jobs/job-dnf").text

    assert "não vai na tarja" in html
    assert '<span lang="en">&#39;DNF&#39;</span>' in html


def test_the_toggle_goes_to_the_orchestrator_with_the_review(ui, fake_orchestrator):
    fake_orchestrator.add_job(pass_result_job("job-send"))

    response = ui.patch(
        "/review/api/jobs/job-send/review",
        json={"expected_version": 3, "action": "save", "show_pass_result": False},
    )

    assert response.status_code == 200, response.text
    # The UI passed the field on, and the fake (like the orchestrator) now
    # says the result is not shown.
    [sent] = fake_orchestrator.requests_to("PATCH", "/jobs/job-send/review")
    assert sent.json_body["show_pass_result"] is False
    assert fake_orchestrator.jobs["job-send"].pass_result.show is False


# --- A clip whose result changed ---


def results(clip_text, now_text):
    return RenderInputs(pass_result_text=clip_text), RenderInputs(pass_result_text=now_text)


def test_a_result_published_after_the_clip_is_put_in_words():
    clip, now = results(None, "66 s · 4 cones → 86 s")
    assert difference_sentence(RenderInputField.PASS_RESULT, clip, now) == (
        "O resultado oficial da passagem foi publicado: “66 s · 4 cones → 86 s”."
    )


def test_a_result_corrected_by_the_judges_is_put_in_words():
    clip, now = results("66 s · 4 cones → 86 s", "66 s · 5 cones → 91 s")
    assert difference_sentence(RenderInputField.PASS_RESULT, clip, now) == (
        "O resultado oficial da passagem foi alterado: o clipe no ar mostra "
        "“66 s · 4 cones → 86 s”; agora seria “66 s · 5 cones → 91 s”."
    )


def test_a_result_no_longer_shown_is_put_in_words():
    # Toggle turned off, or the prova taken out of the configuration.
    clip, now = results("66 s · 4 cones → 86 s", None)
    assert difference_sentence(RenderInputField.PASS_RESULT, clip, now) == (
        "O clipe no ar mostra o resultado “66 s · 4 cones → 86 s”; "
        "a revisão agora diz para não mostrar."
    )


# --- Waiting reason and Frota ---


def test_the_cannot_draw_the_result_reason_matches_the_orchestrator_and_is_pt_br():
    # The UI recognises this exact sentence; a rewording would show English.
    assert NO_HOLDER_CAN_RENDER_PASS_RESULT_REASON == NO_HOLDER_CAN_RENDER_PASS_RESULT
    described = describe_waiting_reason(NO_HOLDER_CAN_RENDER_PASS_RESULT)
    assert "Baja Worker 0.5.0 ou mais novo" in described.text
    assert described.detail is None


def test_frota_names_the_pass_result_feature():
    worker = make_fleet().workers[0].worker
    features = ["framing", "no_audio", "pass_result"]
    capabilities = worker.capabilities.model_copy(update={"render_features": features})
    new = worker.model_copy(update={"capabilities": capabilities})

    assert render_features_text(new) == (
        "Sabe fazer: vertical e girar, sem áudio, resultado da passagem"
    )
