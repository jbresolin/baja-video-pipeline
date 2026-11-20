"""The review UI's side of LIVESTACK-68: the speed shown N seconds before the clip's end.

What this module is for: "Aceleração e Velocidade" may now show its top
speed for the last N seconds of the clip (the configuration's
`speed.seconds_before_end`) instead of from a frame the reviewer marks.
The job page then has no "Velocidade" mark: in its place the "Cronômetro"
block says "Velocidade: entra 3 s antes do fim do clipe.", the v key has
nothing to mark, and the block carries N (data-speed-seconds-before-end)
so the page scripts can draw the green line on the cut bar and the
overlay preview from the form's cut-out. "Testar" says the mode, and the
Settings page explains a bad N in pt-BR. A clip whose speed moved because
the cut-out moved says so in words.

What these tests prove: those pages and sentences, with a fake
orchestrator (`ui`) and, at the end, the live block against the REAL
orchestrator (`reviewer`, `harness`). The mark mode is covered, unchanged,
by test_avf_speed_page.py.

What they do not cover: the page scripts (test_speed_before_end_js.py)
and the orchestrator's rules (tests/orchestrator/test_speed_before_end*.py).

Sample data (sample_data.avf_job): car 1's first AVF run, clock 4,35 s
from a largada at 1.0 s, speed 37,2 km/h; here from 3 s before a 5.0 s
cut-out, so from 2.0 s.
"""

import pytest

from orchestrator.testing.scenarios import hold_raw, make_proxy, receive_job
from review_ui.clip_inputs_view import difference_sentence
from review_ui.orchestrator_client import OrchestratorError
from review_ui.pass_results_settings_view import config_refusal
from review_ui.pass_results_trial_view import trial_speed
from review_ui.pass_speed_view import pass_speed_box
from shared.models import (
    PassResultProblem,
    PassResultTestResponse,
    RenderInputField,
    RenderInputs,
    RenderSpeed,
    WorkerRole,
)
from tests.orchestrator.avf_data import AVF_NAME, set_up_avf
from tests.orchestrator.speed_before_end_data import BEFORE_END_CONFIG_TEXT
from tests.review_ui.english_guard import english_words_in, visible_text
from tests.review_ui.sample_data import T0, avf_job, avf_pass_speed


def before_end_speed(**changes):
    """Car 1's speed, shown 3 s before a 5.0 s cut-out (from 2.0 s), with no mark."""
    fields = {
        "speed_mark_s": None,
        "seconds_before_end": 3,
        "speed_start_s": 2.0,
        "render": RenderSpeed(speed_mark_s=2.0, text="37,2", unit="km/h"),
    }
    fields.update(changes)
    return avf_pass_speed(**fields)


def before_end_job(job_id: str, **speed_changes):
    """Car 1's AVF job, cut 0.5–5.0 s, its prova set to "3 s before the end"."""
    return avf_job(
        job_id,
        cut_in_s=0.5,
        cut_out_s=5.0,
        speed_mark_s=None,
        pass_speed=before_end_speed(**speed_changes),
    )


def clock_block(html: str) -> str:
    """The "Cronômetro" fieldset of a page, from its opening tag to its end."""
    start = html.index('<fieldset class="pass-clock"')
    return html[start : html.index("</fieldset>", start)]


# --- The job page ---


def test_the_mark_controls_are_replaced_by_one_line(ui, fake_orchestrator):
    # Arrange: car 1's AVF job, its prova set to 3 s before the end.
    fake_orchestrator.add_job(before_end_job("job-end"))

    # Act: open the job page.
    block = clock_block(ui.get("/review/jobs/job-end").text)

    # Assert: the line in place of the mark, and none of the mark's controls.
    assert "Velocidade: entra 3 s antes do fim do clipe." in block, "the line must say the mode"
    assert 'name="speed_mark_s"' not in block, "no speed mark field"
    assert 'data-set-mark="speed"' not in block, "no Marcar button"
    assert "data-clear-speed-mark" not in block, "no Limpar"
    assert "pass-speed-early" not in block, "no before-the-largada warning"
    assert "Sem marca de velocidade" not in block, "never asks for a mark"
    assert 'name="largada_s"' in block, "the largada mark stays: the clock needs it"


def test_the_block_carries_n_for_the_page_scripts(ui, fake_orchestrator):
    # Arrange: car 1's AVF job, its prova set to 3 s before the end.
    fake_orchestrator.add_job(before_end_job("job-end-n"))

    # Act: open the job page.
    block = clock_block(ui.get("/review/jobs/job-end-n").text)

    # Assert: N on the fieldset, which js/player.js and the preview read.
    assert 'data-speed-seconds-before-end="3.0"' in block, "the scripts must find N"


def test_the_v_key_is_not_offered_in_the_shortcuts(ui, fake_orchestrator):
    # Arrange: car 1's AVF job, its prova set to 3 s before the end.
    fake_orchestrator.add_job(before_end_job("job-end-keys"))

    # Act: open the job page.
    html = ui.get("/review/jobs/job-end-keys").text

    # Assert: no v shortcut anywhere; the green line still has its place on the cut bar.
    assert "<kbd>v</kbd>" not in html, "v marks nothing in this mode"
    assert "Marcar a velocidade" not in html, "the shortcuts box must not list it"
    assert 'id="speed-mark"' in html, "the cut bar keeps the green line, drawn at the start"


def test_a_decimal_n_is_written_with_a_comma(ui, fake_orchestrator):
    # Arrange: the prova shows the speed for the last 2.5 s.
    fake_orchestrator.add_job(before_end_job("job-end-decimal", seconds_before_end=2.5))

    # Act: open the job page.
    block = clock_block(ui.get("/review/jobs/job-end-decimal").text)

    # Assert: "2,5 s", the Brazilian way.
    assert "Velocidade: entra 2,5 s antes do fim do clipe." in block, "a decimal comma"


def test_the_block_in_this_mode_is_all_in_portuguese(ui, fake_orchestrator):
    # Arrange: car 1's AVF job, its prova set to 3 s before the end.
    fake_orchestrator.add_job(before_end_job("job-end-pt"))

    # Act: open the page and keep the block's visible text.
    block = clock_block(ui.get("/review/jobs/job-end-pt").text)

    # Assert: no English words a reviewer would read.
    assert english_words_in(visible_text(block)) == set(), "the block must be pt-BR"


# Each row: the speed as the orchestrator sends it, the status expected, and why.
STATUS_CASES = [
    # Known and under a clock: on air (the line above already says when).
    pytest.param({}, "Velocidade na tarja: 37,2 km/h, sob o cronômetro.", id="on-air"),
    # Known, but no clock would be drawn: the same sentence as with a mark.
    pytest.param({"render": None}, "A velocidade (37,2 km/h) só entra", id="needs-clock"),
    # No cut-out and no duration: no end of the clip, so no start.
    pytest.param(
        {"render": None, "speed_start_s": None},
        "Sem o fim do clipe (corte final ou duração do vídeo)",
        id="no-clip-end",
    ),
    # Not published yet: the same sentence as with a mark.
    pytest.param(
        {"official_text": None, "render": None, "problem": PassResultProblem.NO_RESULT_YET},
        "Sem velocidade oficial ainda:",
        id="not-published",
    ),
]


@pytest.mark.parametrize(("changes", "expected_start"), STATUS_CASES)
def test_the_status_line_says_what_will_be_burned_in(changes, expected_start):
    # Arrange: car 1's speed, 3 s before the end, with the row's changes.
    speed = before_end_speed(**changes)

    # Act: build the speed row of the block.
    box = pass_speed_box(speed, T0)

    # Assert: the sentence the page shows, and the mode line above it.
    assert box.status.startswith(expected_start), f"status must start with {expected_start!r}"
    assert box.before_end_line == "Velocidade: entra 3 s antes do fim do clipe."


def test_mark_mode_has_no_before_end_line():
    # Arrange: car 1's speed from a mark (the configuration has no N).
    speed = avf_pass_speed()

    # Act: build the speed row.
    box = pass_speed_box(speed, T0)

    # Assert: no line and no N: the page shows the mark as before.
    assert box.before_end_line is None, "the mark mode keeps its controls"
    assert box.seconds_before_end is None, "and the block carries no N"


# --- "Testar" ---


def test_testar_says_the_speed_comes_in_3_s_before_the_end():
    # Arrange: the orchestrator's answer for car 1's first run, in this mode.
    answer = PassResultTestResponse(
        speed_configured=True, speed_text="37,2", speed_unit="km/h", speed_seconds_before_end=3
    )

    # Act: the speed line of the outcome.
    line = trial_speed(answer)

    # Assert: the brief's sentence, the value in bold.
    expected = "Velocidade: <strong>37,2 km/h</strong> (entra 3 s antes do fim do clipe)."
    assert str(line) == expected, "Testar must say the mode"


# --- Configurações: a bad N, in pt-BR ---

# Each row: the 422's details, what the sentence must say, and why the row exists.
REFUSALS = [
    # 0, a negative number or more than 30.
    pytest.param(
        {"problem": "more_than_and_at_most", "more_than": 0, "maximum": 30},
        "o valor deve ser maior que 0 e no máximo 30",
        id="out-of-range",
    ),
    # Text in quotes, true/false, NaN.
    pytest.param(
        {"problem": "wrong_type", "expected": "number"},
        "o valor deve ser um número",
        id="not-a-number",
    ),
]


@pytest.mark.parametrize(("details", "expected"), REFUSALS)
def test_a_bad_seconds_before_end_is_explained_in_portuguese(details, expected):
    # Arrange: the orchestrator's 422 for the bad value.
    path = "provas.AVF.speed.seconds_before_end"
    error = OrchestratorError(422, "validation_error", f"{path}: bad", {"path": path, **details})

    # Act: the sentence the Settings page shows.
    sentence = config_refusal(error, for_test=False)

    # Assert: the place and the reason in pt-BR, without the English message.
    assert f"<code>{path}</code>" in sentence, "the page must point at the key"
    assert expected in sentence, "and say what is wrong"
    assert 'lang="en"' not in sentence, "a known problem needs no English"


# --- A clip whose speed moved ---


def test_a_speed_that_only_moved_says_so_without_claiming_it_changed():
    # Arrange: the clip on air has 37,2 km/h from 2.0 s; the cut-out moved,
    # so a render now would start it at 3.0 s, with the same value.
    clip = RenderInputs(pass_speed=RenderSpeed(speed_mark_s=2.0, text="37,2", unit="km/h"))
    now = RenderInputs(pass_speed=RenderSpeed(speed_mark_s=3.0, text="37,2", unit="km/h"))

    # Act: the sentence for the "speed" difference.
    sentence = difference_sentence(RenderInputField.SPEED, clip, now)

    # Assert: it says where it starts, on air and now, and not that the speed changed.
    assert sentence == (
        "A velocidade (37,2 km/h) entra em outro momento: no clipe no ar a partir de 0:02,000; "
        "agora seria a partir de 0:03,000."
    )


# --- Against the real orchestrator ---


def test_choosing_avf_brings_the_line_and_n_without_a_save(reviewer, harness):
    # Arrange: AVF set to 3 s before the end; a new video, nothing saved.
    set_up_avf(harness, config_text=BEFORE_END_CONFIG_TEXT)
    worker = harness.enroll(WorkerRole.MASTER, "new-master")
    job_id = receive_job(harness)
    hold_raw(harness, worker, job_id)
    make_proxy(harness, worker, job_id)

    # Act: what the page asks while the reviewer picks AVF, car 1, passagem 1.
    answer = reviewer.get(
        f"/review/api/jobs/{job_id}/pass-blocks",
        params={"prova": AVF_NAME, "car_number": 1, "passagem": 1},
    ).json()

    # Assert: the clock block with the line and N, and no speed mark.
    clock_html = answer["clock_html"]
    assert "Velocidade: entra 3 s antes do fim do clipe." in clock_html, "the mode's line"
    assert 'data-speed-seconds-before-end="3.0"' in clock_html, "N for the page scripts"
    assert 'name="speed_mark_s"' not in clock_html, "no speed mark"
    assert answer["preview"]["speed"] == {"text": "37,2", "unit": "km/h"}, "the preview's speed"
