"""Edits after delivery are visible and reprocessable (LIVESTACK-43), in the review UI.

What happened: João changed the cut-in of a job whose clip was already on
the studio box. Saving said "salvo, v2", but nothing told him the clip on
air no longer matched the form, and he did not notice the reprocess
button. Now:

* the job page shows a warning banner listing every input that differs,
  in plain words ("O clipe no ar usa corte 0:03–0:12; a revisão agora diz
  0:05–0:12.");
* the clips page marks such clips "desatualizado" (details on expand);
* the queue has a "Clipes desatualizados" filter;
* once a clip exists, the primary button is "Salvar e reprocessar" (one
  request that saves and reprocesses); plain "Salvar" stays, and its
  confirmation says the clip on air does not change until reprocessed.

The orchestrator computes which inputs differ (Job.clip_inputs, tested in
tests/orchestrator/test_render_inputs.py); these tests check what the UI
makes of it. Setup is in conftest.py and sample_data.py.
"""

import pytest

from review_ui.clip_inputs_view import difference_sentence, outdated_clip
from review_ui.job_view import action_buttons
from shared.models import JobState, NameStyle, RenderInputField
from tests.review_ui.sample_data import (
    comparison,
    delivered_job,
    edited_after_delivery_job,
    rendered_inputs,
)
from tests.review_ui.test_templates_render import job_context, render

REVIEW_URL = "/review/api/jobs/{}/review"


# --- The sentences (clip_inputs_view.py) ---


def test_the_cut_sentence_reads_like_the_brief():
    clip = rendered_inputs(cut_in_s=3.0, cut_out_s=12.0)
    now = rendered_inputs(cut_in_s=5.0, cut_out_s=12.0)
    assert difference_sentence(RenderInputField.CUT, clip, now) == (
        "O clipe no ar usa corte 0:03–0:12; a revisão agora diz 0:05–0:12."
    )


def test_a_cut_moved_by_less_than_a_second_shows_milliseconds():
    # Whole seconds would read "0:03–0:12" on both sides and hide the change.
    clip = rendered_inputs(cut_in_s=3.0, cut_out_s=12.0)
    now = rendered_inputs(cut_in_s=3.25, cut_out_s=12.0)
    assert difference_sentence(RenderInputField.CUT, clip, now) == (
        "O clipe no ar usa corte 0:03,000–0:12,000; a revisão agora diz 0:03,250–0:12,000."
    )


@pytest.mark.parametrize(
    ("field", "now_changes", "expected"),
    [
        (
            RenderInputField.PROVA,
            {"prova": "Enduro"},
            "O clipe no ar é da prova Suspensão; a revisão agora diz Enduro.",
        ),
        (
            RenderInputField.CAR_NUMBER,
            {"car_number": 70},
            "O clipe no ar mostra o carro 7; a revisão agora diz 70.",
        ),
        (
            RenderInputField.PASSAGEM,
            {"passagem": 2},
            "O clipe no ar mostra a passagem 1; a revisão agora diz 2.",
        ),
        (
            RenderInputField.SHOW_PASS,
            {"show_pass": False},
            "O clipe no ar mostra o número da passagem; a revisão agora diz para não mostrar.",
        ),
        (
            RenderInputField.RAW,
            {"raw_sha256": "f" * 64},
            "O clipe no ar foi feito de outro arquivo original.",
        ),
    ],
)
def test_one_sentence_per_review_field(field, now_changes, expected):
    assert difference_sentence(field, rendered_inputs(), rendered_inputs(**now_changes)) == expected


def test_a_name_choice_changed_in_settings_says_so():
    # The admin switched the team name to "completo" after the clip was made.
    clip = rendered_inputs(overlay_team_name="Baja UFMG")
    now = rendered_inputs(overlay_team_name="BAJA UFMG", team_name_style=NameStyle.FULL)
    assert difference_sentence(RenderInputField.TEAM_NAME, clip, now) == (
        "O clipe no ar mostra a equipe “Baja UFMG”; agora seria “BAJA UFMG” "
        "(nome completo nas Configurações)."
    )
    # A school that was left out of the clip (unknown then) reads "nenhum".
    clip = rendered_inputs(overlay_school_name=None)
    now = rendered_inputs(overlay_school_name="UFMG")
    assert difference_sentence(RenderInputField.SCHOOL_NAME, clip, now) == (
        "O clipe no ar mostra a escola “nenhum”; agora seria “UFMG”."
    )


def test_nothing_to_say_when_the_clip_matches_or_nothing_is_known():
    matching = comparison(rendered_inputs(), rendered_inputs())
    assert outdated_clip(matching) is None
    # A clip rendered before render inputs were recorded: no comparison.
    assert outdated_clip(None) is None


def test_the_closing_sentence_depends_on_whether_a_new_version_is_coming():
    changed = comparison(
        rendered_inputs(), rendered_inputs(passagem=2), RenderInputField.PASSAGEM, version=1
    )
    done = outdated_clip(changed, JobState.DONE)
    assert done.next_step.startswith("Nada muda no ar até você usar “Salvar e reprocessar”")
    assert "versão 2" in done.next_step
    # After "Salvar e reprocessar" the job is ready: version 2 is on its way.
    coming = outdated_clip(changed, JobState.READY)
    assert coming.next_step.startswith("A versão 2 já está na fila")


# --- The job page ---


def test_the_job_page_warns_that_the_clip_on_air_no_longer_matches(ui, fake_orchestrator):
    fake_orchestrator.add_job(edited_after_delivery_job("job-edited-1"))

    page = ui.get("/review/jobs/job-edited-1")

    assert page.status_code == 200
    assert 'id="clip-outdated"' in page.text
    assert "O clipe no ar (versão 1) não corresponde mais à revisão:" in page.text
    # sample_data: the cut-in moved from 1 s to 3 s after delivery.
    assert "O clipe no ar usa corte 0:01–0:20; a revisão agora diz 0:03–0:20." in page.text


def test_the_warning_is_part_of_the_refreshed_status_strip(ui, fake_orchestrator):
    # The page polls the panel every few seconds, so the banner appears
    # (and disappears when version 2 arrives) without a reload.
    fake_orchestrator.add_job(edited_after_delivery_job("job-edited-1"))
    panel = ui.get("/review/jobs/job-edited-1/panel")
    assert 'id="clip-outdated"' in panel.text


def test_no_warning_for_a_clip_that_matches(ui):
    # job-done-1 (conftest) has a clip and no differences.
    page = ui.get("/review/jobs/job-done-1")
    assert 'id="clip-outdated"' not in page.text


@pytest.mark.parametrize("state", [JobState.UPLOADED, JobState.DELIVERED, JobState.DONE])
def test_salvar_e_reprocessar_is_the_first_and_primary_button(state):
    # "enviado", "entregue" and "concluído": a clip exists, so the obvious
    # action after an edit is to render it again.
    html = render("job.html", job_context(delivered_job().model_copy(update={"state": state})))

    buttons = html[html.index('<div class="actions">') :]
    reprocess = buttons.index('data-action="reprocess"')
    save = buttons.index('data-action="save"')
    assert reprocess < save
    assert (
        'class="action action-reprocess primary" data-action="reprocess">Salvar e reprocessar'
        in (buttons)
    )
    # Plain "Salvar" is still there, as a secondary button.
    assert 'class="action action-save" data-action="save">Salvar<' in buttons


def test_before_a_clip_exists_salvar_comes_first_and_pronto_is_primary():
    buttons = action_buttons(JobState.AWAITING_REVIEW)
    assert [(b.value, b.primary) for b in buttons] == [
        ("save", False),
        ("ready", True),
        ("discard", False),
    ]


def test_salvar_e_reprocessar_is_one_request_that_saves_and_reprocesses(ui, fake_orchestrator):
    # The reviewer moves the cut-in and presses "Salvar e reprocessar".
    body = {"expected_version": 3, "action": "reprocess", "cut_in_s": 5.0, "cut_out_s": 20.0}

    response = ui.patch(REVIEW_URL.format("job-done-1"), json=body)

    assert response.status_code == 200
    # Exactly one PATCH reached the orchestrator, carrying both the new
    # fields and the reprocess action...
    [sent] = fake_orchestrator.requests_to("PATCH", "/jobs/job-done-1/review")
    assert sent.json_body == body
    # ...and the job is saved and back to ready for a new rendering.
    job = fake_orchestrator.jobs["job-done-1"]
    assert (job.state, job.cut_in_s) == (JobState.READY, 5.0)
    assert response.json()["message"].startswith("Salvo e enviado para reprocessar")


def test_a_plain_save_on_a_delivered_job_says_the_clip_on_air_will_not_change(
    ui, fake_orchestrator
):
    fake_orchestrator.add_job(edited_after_delivery_job("job-edited-1"))

    response = ui.patch(
        REVIEW_URL.format("job-edited-1"),
        json={"expected_version": 3, "action": "save", "cut_in_s": 4.0},
    )

    answer = response.json()
    assert answer["message"] == (
        "Salvo. O clipe no ar não muda até você usar “Salvar e reprocessar”."
    )
    # The page reloads after this save so the warning banner shows at once.
    assert answer["refresh_page"] is True
    # The job itself was only saved: still done.
    assert fake_orchestrator.jobs["job-edited-1"].state == JobState.DONE


def test_a_plain_save_before_any_clip_is_just_saved(ui):
    response = ui.patch(
        REVIEW_URL.format("job-awaiting-1"),
        json={"expected_version": 3, "action": "save", "cut_in_s": 4.0},
    )
    assert response.json()["message"] == "Salvo."
    assert response.json()["refresh_page"] is False


# --- The clips page and the queue ---


def test_the_clips_page_marks_outdated_clips_with_the_detail(ui):
    # sample_data's manifest: the older clip's passagem changed after delivery.
    page = ui.get("/review/clips")

    assert page.status_code == 200
    assert page.text.count('<details class="outdated"') == 1
    assert ">desatualizado<" in page.text
    assert "O clipe no ar mostra a passagem 1; a revisão agora diz 2." in page.text


def test_the_queue_filter_asks_the_orchestrator_for_outdated_clips(ui, fake_orchestrator):
    fake_orchestrator.add_job(edited_after_delivery_job("job-edited-1"))

    page = ui.get("/review/?outdated=1")

    assert page.status_code == 200
    # Every state a job with a clip can be in, and only outdated ones.
    [sent] = fake_orchestrator.requests_to("GET", "/review/queue")
    states = [value for name, value in sent.query if name == "state"]
    assert states == ["ready", "claimed", "uploaded", "delivered", "done"]
    assert ("outdated", "true") in sent.query
    assert "/review/jobs/job-edited-1" in page.text
    assert "/review/jobs/job-done-1" not in page.text  # delivered, but up to date
    assert 'title="O clipe no ar não corresponde mais à revisão">desatualizado' in page.text


def test_the_queue_offers_the_preset_and_the_checkbox(ui):
    page = ui.get("/review/")
    assert 'href="/review/?outdated=1">Clipes desatualizados</a>' in page.text
    assert 'name="outdated" value="1"' in page.text


def test_only_outdated_rows_carry_the_badge(ui, fake_orchestrator):
    fake_orchestrator.add_job(edited_after_delivery_job("job-edited-1"))
    page = ui.get("/review/?state=done")
    # job-done-1 and job-edited-1 are both done; only the edited one is outdated.
    assert "/review/jobs/job-done-1" in page.text
    assert page.text.count(">desatualizado</span>") == 1
