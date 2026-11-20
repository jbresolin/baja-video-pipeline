"""The review UI's side of LIVESTACK-67: the speed mark, its status, and the speed in words.

What this module is for: for "Aceleração e Velocidade" the job page's
"Cronômetro" block gets a second mark, "Velocidade" (where the car
crosses the speed trap), with a line saying what will be burned in: the
speed under the clock from the mark, or what is missing (the mark, the
clock, the official speed). The Settings page's "Testar" says the speed
too, and lists the speed's own table. A clip whose speed changed says so
in words; the waiting sentence and Frota's feature name are in pt-BR.

What these tests prove: those pages and sentences, with a fake
orchestrator (`ui`), and, at the end, the live block against the REAL
orchestrator (`reviewer`, `harness`) with the AVF fixtures.

What they do not cover: the page scripts (test_avf_speed_js.py) and the
orchestrator's rules (tests/orchestrator/test_avf_speed*.py).

Sample data (sample_data.avf_job): car 1's first AVF run, clock 4,35 s
from a largada at 1.0 s, speed 37,2 km/h marked at 4.0 s.
"""

import json
from datetime import timedelta

import pytest

from orchestrator.domain.render_features import NO_HOLDER_CAN_RENDER_SPEED
from orchestrator.testing.scenarios import hold_raw, make_proxy, receive_job
from review_ui.clip_inputs_view import difference_sentence
from review_ui.clips_and_fleet_view import render_features_text
from review_ui.job_view import NO_HOLDER_CAN_RENDER_SPEED_REASON, describe_waiting_reason
from review_ui.orchestrator_client import OrchestratorError
from review_ui.pass_results_settings_view import config_refusal, speed_status_row
from review_ui.pass_results_trial_view import trial_speed
from review_ui.pass_speed_view import pass_speed_box
from shared.models import (
    PassResultFetch,
    PassResultProblem,
    PassResultsProvaStatus,
    PassResultTestResponse,
    RenderInputField,
    RenderInputs,
    RenderSpeed,
    WorkerRole,
)
from tests.orchestrator.avf_data import AVF_NAME, set_up_avf
from tests.review_ui.english_guard import english_words_in, visible_text
from tests.review_ui.sample_data import (
    T0,
    avf_job,
    avf_pass_speed,
    failing_fetch,
    make_fleet,
    make_job,
)

# --- The job page ---


def test_an_avf_job_has_the_speed_mark_next_to_the_largada(ui, fake_orchestrator):
    # Arrange: car 1's AVF job with both marks set and the speed on air.
    fake_orchestrator.add_job(avf_job("job-avf"))

    # Act: open the job page.
    html = ui.get("/review/jobs/job-avf").text

    # Assert: the speed field with its value, its buttons and its line on the cut bar.
    assert 'name="speed_mark_s"' in html, "the speed mark field must be on the page"
    assert 'value="4.000"' in html, "with the saved mark"
    assert 'data-set-mark="speed"' in html, "a Marcar button for it"
    assert "data-clear-speed-mark" in html, "and Limpar"
    assert 'id="speed-mark"' in html, "a second line on the cut bar"
    assert "<kbd>v</kbd>" in html, "the v key, on the button and in the shortcuts"
    assert "Marcar a velocidade" in html, "the shortcuts box names it"
    assert "Velocidade na tarja: 37,2 km/h" in html, "the status says what goes on air"


def test_a_prova_without_a_speed_has_no_speed_mark(ui, fake_orchestrator):
    # Arrange: car 1's AVF-like job whose prova has a clock but no speed.
    fake_orchestrator.add_job(avf_job("job-clock-only", pass_speed=None, speed_mark_s=None))

    # Act: open the job page.
    html = ui.get("/review/jobs/job-clock-only").text

    # Assert: the clock block is there, the speed mark is not.
    assert 'name="largada_s"' in html, "the clock block stays"
    assert 'name="speed_mark_s"' not in html, "no speed, no speed mark"
    assert "Marcar a velocidade" not in html, "and no v shortcut"


def test_the_speed_block_is_all_in_portuguese(ui, fake_orchestrator):
    # Arrange: an AVF job whose speed table cannot be fetched.
    speed = avf_pass_speed(fetch=failing_fetch(resultado_id="26BR_VEL"))
    fake_orchestrator.add_job(avf_job("job-avf-pt", pass_speed=speed))

    # Act: open the page and keep the "Cronômetro" block's visible text.
    html = ui.get("/review/jobs/job-avf-pt").text
    start = html.index('id="pass-clock"')
    block = html[start : html.index("</fieldset>", start)]

    # Assert: no English words a reviewer would read (the API's own error
    # is marked lang="en" and left out by the guard).
    assert english_words_in(visible_text(block)) == set(), "the block must be pt-BR"


# Each row: the speed as the orchestrator sends it, the start of the status, and why.
STATUS_CASES = [
    # Marked, known and under a clock: on air.
    pytest.param({}, "Velocidade na tarja: 37,2 km/h", id="on-air"),
    # Known, not marked: the page says the clip goes without it.
    pytest.param({"speed_mark_s": None, "render": None}, "Sem marca de velocidade", id="no-mark"),
    # Known and marked but no clock would be drawn (no largada, toggle off).
    pytest.param({"render": None}, "A velocidade (37,2 km/h) só entra", id="needs-clock"),
    # Not published yet.
    pytest.param(
        {"official_text": None, "render": None, "problem": PassResultProblem.NO_RESULT_YET},
        "Sem velocidade oficial ainda:",
        id="not-published",
    ),
]


@pytest.mark.parametrize(("changes", "expected_start"), STATUS_CASES)
def test_the_status_line_says_what_will_be_burned_in(changes, expected_start):
    # Arrange: car 1's speed with the row's changes.
    speed = avf_pass_speed(**changes)

    # Act: build the speed row of the block.
    box = pass_speed_box(speed, T0)

    # Assert: the sentence the page shows.
    assert box.status.startswith(expected_start), f"status must start with {expected_start!r}"


def test_a_failing_speed_table_is_said_under_the_speed(ui, fake_orchestrator):
    # Arrange: the speed table's last fetch failed.
    speed = avf_pass_speed(fetch=failing_fetch(resultado_id="26BR_VEL"))

    # Act: build the speed row.
    box = pass_speed_box(speed, T0 + timedelta(minutes=5))

    # Assert: the warning and the API's own error.
    assert box.fetch_warning.startswith("API de resultados inacessível"), "the page warns"
    assert "connection refused" in box.fetch_error, "with the error as the API gave it"


def test_the_speed_mark_goes_to_the_orchestrator(ui, fake_orchestrator):
    # Arrange: an AVF job.
    fake_orchestrator.add_job(avf_job("job-send-speed"))

    # Act: save a new speed mark through the UI's API, as the page does.
    response = ui.patch(
        "/review/api/jobs/job-send-speed/review",
        json={"expected_version": 3, "action": "save", "speed_mark_s": 4.5},
    )

    # Assert: passed on to the orchestrator unchanged.
    assert response.status_code == 200, response.text
    [sent] = fake_orchestrator.requests_to("PATCH", "/jobs/job-send-speed/review")
    assert sent.json_body["speed_mark_s"] == 4.5, "the mark must reach the orchestrator"


def test_removing_the_speed_mark_goes_to_the_orchestrator(ui, fake_orchestrator):
    # Arrange: an AVF job with a speed mark.
    fake_orchestrator.add_job(avf_job("job-clear-speed"))

    # Act: "Limpar" then save: the page sends clear_speed_mark.
    response = ui.patch(
        "/review/api/jobs/job-clear-speed/review",
        json={"expected_version": 3, "action": "save", "clear_speed_mark": True},
    )

    # Assert: passed on, and the (fake) orchestrator removed it.
    assert response.status_code == 200, response.text
    [sent] = fake_orchestrator.requests_to("PATCH", "/jobs/job-clear-speed/review")
    assert sent.json_body["clear_speed_mark"] is True, "the flag must reach the orchestrator"
    assert fake_orchestrator.jobs["job-clear-speed"].speed_mark_s is None, "the mark is gone"


def test_the_overlay_preview_gets_the_speed(ui, fake_orchestrator):
    # Arrange: an AVF job.
    fake_orchestrator.add_job(avf_job("job-preview-speed"))

    # Act: open the page and read the preview's embedded data.
    html = ui.get("/review/jobs/job-preview-speed").text
    start = html.index('<script type="application/json" id="overlay-preview-data">')
    end = html.index("</script>", start)
    data = json.loads(html[html.index(">", start) + 1 : end])

    # Assert: the speed's text and unit (the mark is read live from the form).
    assert data["speed"] == {"text": "37,2", "unit": "km/h"}, "the preview draws 37,2 km/h"
    assert data["clock"]["official_units"] == 435, "under the clock stopping at 4,35"


# --- A clip whose speed changed ---

SPEED = RenderSpeed(speed_mark_s=4.0, text="37,2", unit="km/h")
CORRECTED = RenderSpeed(speed_mark_s=4.0, text="36,5", unit="km/h")


def test_a_speed_that_would_now_appear_is_put_in_words():
    # Arrange: the clip has no speed; a render now would draw one.
    clip, now = RenderInputs(), RenderInputs(pass_speed=SPEED)

    # Act: the sentence for the "speed" difference.
    sentence = difference_sentence(RenderInputField.SPEED, clip, now)

    # Assert: it names the speed and where it starts.
    assert sentence == "A velocidade agora entraria no clipe: 37,2 km/h a partir de 0:04,000."


def test_a_corrected_speed_is_put_in_words():
    # Arrange: the judges corrected 37,2 to 36,5 after the clip was made.
    clip, now = RenderInputs(pass_speed=SPEED), RenderInputs(pass_speed=CORRECTED)

    # Act: the sentence for the "speed" difference.
    sentence = difference_sentence(RenderInputField.SPEED, clip, now)

    # Assert: both values, the clip's and now's.
    assert sentence == (
        "A velocidade mudou: o clipe no ar tem 37,2 km/h a partir de 0:04,000; "
        "agora seria 36,5 km/h a partir de 0:04,000."
    )


def test_a_speed_taken_out_is_put_in_words():
    # Arrange: the clip has a speed; the reviewer cleared the mark.
    clip, now = RenderInputs(pass_speed=SPEED), RenderInputs()

    # Act: the sentence for the "speed" difference.
    sentence = difference_sentence(RenderInputField.SPEED, clip, now)

    # Assert: it says the clip on air has a speed the review no longer wants.
    assert sentence.startswith("O clipe no ar tem velocidade (37,2 km/h a partir de 0:04,000)")


# --- Waiting reason and Frota ---


def test_the_waiting_reason_is_the_orchestrators_and_reads_in_portuguese():
    # Arrange: the sentence the orchestrator writes for a 0.7.0 holder.
    reason = NO_HOLDER_CAN_RENDER_SPEED

    # Act: put it in pt-BR, as the job page does.
    described = describe_waiting_reason(reason)

    # Assert: the UI knows it (same text) and names the version to install.
    assert reason == NO_HOLDER_CAN_RENDER_SPEED_REASON, "the UI must match the orchestrator"
    assert "Baja Worker 0.8.0" in described.text, "the page must name 0.8.0"
    assert described.detail is None, "nothing left in English"


def test_frota_names_the_speed_feature_in_portuguese():
    # Arrange: a worker declaring only the speed (the others are named elsewhere).
    worker = make_fleet().workers[0].worker
    capabilities = worker.capabilities.model_copy(update={"render_features": ["speed"]})
    with_speed = worker.model_copy(update={"capabilities": capabilities})

    # Act: the features in words, as Frota shows them.
    shown = render_features_text(with_speed)

    # Assert: the pt-BR name.
    assert shown == "Sabe fazer: velocidade sob o cronômetro", "Frota must name the feature"


# --- Configurações ---


def test_testar_says_the_speed_with_its_unit():
    # Arrange: the orchestrator's answer for car 1's first AVF run.
    answer = PassResultTestResponse(speed_configured=True, speed_text="37,2", speed_unit="km/h")

    # Act: the speed line of the outcome.
    line = trial_speed(answer)

    # Assert: "Velocidade: 37,2 km/h." with the value in bold.
    assert str(line) == "Velocidade: <strong>37,2 km/h</strong>.", "Testar must show the speed"


def test_testar_says_why_there_is_no_speed():
    # Arrange: car 2's second run, never run.
    answer = PassResultTestResponse(
        speed_configured=True, speed_problem=PassResultProblem.NO_RESULT_YET
    )

    # Act: the speed line.
    line = trial_speed(answer)

    # Assert: the reason, in pt-BR.
    assert line.startswith("Velocidade: "), "the line names the speed"
    assert english_words_in(line) == set(), "and is all pt-BR"


def test_testar_says_nothing_about_a_speed_for_a_prova_without_one():
    # Arrange: Manobrabilidade's answer (no speed block).
    answer = PassResultTestResponse(text="66 s")

    # Act and assert: no speed line at all.
    assert trial_speed(answer) is None, "no speed block, no speed line"


def test_the_speed_table_has_its_own_status_row():
    # Arrange: AVF's status with both tables, the speed's fetched once.
    prova = PassResultsProvaStatus(
        prova_id="AVF",
        prova_name=AVF_NAME,
        resultado_id="26BR_ACE",
        polling=True,
        speed_resultado_id="26BR_VEL",
        speed_polling=True,
        speed_fetch=PassResultFetch(
            resultado_id="26BR_VEL", last_attempt_at=T0, last_attempt_ok=True, last_success_at=T0
        ),
    )

    # Act: the speed table's row.
    row = speed_status_row(prova, T0)

    # Assert: its own table, marked as the speed's, with its own status.
    assert row.table == "26BR_VEL", "the row is the speed table's"
    assert row.table_role == "velocidade", "and says what it is for"
    assert row.last_fetch_failed is False, "with its own fetch status"


def test_a_speed_without_a_clock_is_refused_in_portuguese():
    # Arrange: the orchestrator's 422 for a speed block without a clock.
    error = OrchestratorError(
        422,
        "validation_error",
        "provas.AVF.speed: needs a 'clock' block too",
        {"path": "provas.AVF.speed", "problem": "speed_needs_clock"},
    )

    # Act: the sentence the Settings page shows.
    sentence = config_refusal(error, for_test=False)

    # Assert: the place and the reason in pt-BR, without the English message.
    assert "<code>provas.AVF.speed</code>" in sentence, "the page must point at the speed block"
    assert "precisa de um bloco <code>clock</code>" in sentence, "and say why"
    assert 'lang="en"' not in sentence, "a known problem needs no English"


# --- Against the real orchestrator ---


def test_choosing_avf_on_a_new_video_brings_the_speed_mark_without_a_save(reviewer, harness):
    # Arrange: AVF configured, both tables served; a new video, nothing saved.
    set_up_avf(harness)
    worker = harness.enroll(WorkerRole.MASTER, "new-master")
    job_id = receive_job(harness)
    hold_raw(harness, worker, job_id)
    make_proxy(harness, worker, job_id)
    page = reviewer.get(f"/review/jobs/{job_id}").text
    assert 'name="speed_mark_s"' not in page, "no prova yet, no speed mark"

    # Act: what the page asks while the reviewer picks AVF, car 1, passagem 1.
    answer = reviewer.get(
        f"/review/api/jobs/{job_id}/pass-blocks",
        params={"prova": AVF_NAME, "car_number": 1, "passagem": 1},
    ).json()

    # Assert: the clock block with both marks, the official speed, and the
    # preview's speed, before any save.
    clock_html = answer["clock_html"]
    assert 'name="largada_s"' in clock_html, "the largada mark is there"
    assert 'name="speed_mark_s"' in clock_html, "and the speed mark"
    assert "37,2 km/h" in clock_html, "the status names the official speed"
    assert answer["preview"]["speed"] == {"text": "37,2", "unit": "km/h"}, "for the preview too"


def test_a_job_without_the_speed_block_never_mentions_the_speed(ui, fake_orchestrator):
    # Arrange: a plain job of a prova without results.
    fake_orchestrator.add_job(make_job("job-plain"))

    # Act: open it.
    html = ui.get("/review/jobs/job-plain").text

    # Assert: no speed anywhere.
    assert "speed_mark_s" not in html, "no speed field"
    assert "Velocidade na tarja" not in html, "no speed status"
