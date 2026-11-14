"""The review UI's side of waiting for the official result (LIVESTACK-62), on the REAL orchestrator.

What this module is for: next to "Pronto" the job page has "Esperar o
resultado oficial (até 15 min)", ticked by default. While the render waits,
the status strip says until when and offers "Renderizar agora", the queue
marks the row "⏳ aguardando resultado até 09:15", and the event log tells
the story in pt-BR. The Settings page holds the number of minutes.

What these tests prove: the box is offered (and shown) only for a prova
with results; one whole path from "Pronto" to the release by the result,
through the review UI and the real orchestrator, exactly as the page
script sends it; "Renderizar agora" through the UI; the Settings field.

What they do not cover: the rules of the wait itself (tests/orchestrator/
test_result_hold*.py) and the page scripts (test_result_wait_js.py).

The orchestrator's clock starts at 2026-10-25 12:00 UTC, which is 09:00 in
São Paulo, so the default 15-minute wait ends at 09:15 on the page.
"""

from orchestrator.testing.scenarios import ask_next, ensure_prova
from shared.models import TaskKind
from tests.orchestrator.result_hold_helpers import (
    CAR_5_RUN_2,
    job_waiting_for_review,
    manobrabilidade_and_master,
)
from tests.orchestrator.test_pass_results_render import judges_publish
from tests.orchestrator.test_render_inputs import edit_review
from tests.review_ui.admin_browser import follow, post_form

# What the job page sends on "Pronto" with the box ticked (js/review_form.js).
PRONTO_WITH_THE_BOX = {
    "action": "ready",
    "cut_in_s": 1.0,
    "cut_out_s": 5.0,
    "wait_for_result": True,
}


def click_pronto_in_the_page(reviewer, harness, job_id):
    """The reviewer clicks "Pronto" on the job page with the box ticked."""
    version = harness.job(job_id)["version"]
    body = {"expected_version": version, **PRONTO_WITH_THE_BOX}
    response = reviewer.patch(f"/review/api/jobs/{job_id}/review", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def test_the_box_is_offered_ticked_for_a_prova_with_results(reviewer, harness):
    # Arrange: a Manobrabilidade video in review (its result line is on by default).
    master = manobrabilidade_and_master(harness)
    job_id = job_waiting_for_review(harness, master, car=5, passagem=2)

    # Act: the reviewer opens the job page.
    page = reviewer.get(f"/review/jobs/{job_id}").text

    # Assert: the box is there, ticked, enabled, with the Settings' 15 minutes.
    assert "Esperar o resultado oficial (até 15 min)" in page, "the label names the wait"
    assert 'name="wait_for_result" checked>' in page, "ticked and enabled by default"
    assert 'id="result-wait" title=' in page and 'id="result-wait" hidden' not in page, (
        "shown, since the result line is on"
    )


def test_a_prova_without_results_gets_no_visible_box(reviewer, harness):
    # Arrange: the video is saved as Enduro, which has no results configuration.
    master = manobrabilidade_and_master(harness)
    job_id = job_waiting_for_review(harness, master, car=5, passagem=2)
    ensure_prova(harness, "Enduro")
    edit_review(harness, job_id, prova="Enduro")

    # Act: the reviewer opens the job page.
    page = reviewer.get(f"/review/jobs/{job_id}").text

    # Assert: the box is hidden and disabled, so "Pronto" does not send it.
    assert 'id="result-wait" hidden' in page, "no visible box for a prova without results"
    assert 'name="wait_for_result" checked disabled' in page, "a hidden box is never sent"


def test_from_pronto_to_the_release_by_the_official_result(reviewer, harness):
    # Arrange: car 5's second run in review; its result is not in yet.
    master = manobrabilidade_and_master(harness)
    job_id = job_waiting_for_review(harness, master, car=5, passagem=2)

    # Act: "Pronto" with the box ticked, through the review UI.
    answer = click_pronto_in_the_page(reviewer, harness, job_id)

    # Assert: the job waits, and every place says so in pt-BR.
    assert answer["job"]["result_hold_until"] is not None, "the orchestrator holds the render"
    panel = reviewer.get(f"/review/jobs/{job_id}/panel").text
    assert "Aguardando o resultado oficial (até 09:15)." in panel, "the strip says until when"
    assert "Renderizar agora" in panel, "and offers to stop waiting"
    assert "renderização esperando o resultado oficial até 09:15" in panel, "the log says it too"
    queue = reviewer.get("/review/?state=ready").text
    assert "⏳ aguardando resultado até 09:15" in queue, "the queue row is marked"
    assert ask_next(harness, master, [TaskKind.PROCESS]) is None, "no PC gets the render yet"

    # Act again: the judges enter the run and the next fetch sees it.
    judges_publish(harness, 5, 2, *CAR_5_RUN_2)

    # Assert: released; the banner is gone, the log says why, the PC gets the render.
    panel = reviewer.get(f"/review/jobs/{job_id}/panel").text
    assert "Aguardando o resultado oficial" not in panel, "the banner goes with the hold"
    assert "resultado oficial chegou: renderização liberada" in panel
    assert ask_next(harness, master, [TaskKind.PROCESS])["job_id"] == job_id


def test_renderizar_agora_through_the_page(reviewer, harness):
    # Arrange: car 5's job held for its result.
    master = manobrabilidade_and_master(harness)
    job_id = job_waiting_for_review(harness, master, car=5, passagem=2)
    click_pronto_in_the_page(reviewer, harness, job_id)

    # Act: the reviewer clicks "Renderizar agora" in the status strip (js/job.js).
    response = reviewer.post(f"/review/api/jobs/{job_id}/render-now", json={})

    # Assert: a pt-BR message, the banner gone, the render offered, the reviewer in the log.
    assert response.status_code == 200, response.text
    assert response.json()["message"].startswith("Renderização liberada"), "said in pt-BR"
    panel = reviewer.get(f"/review/jobs/{job_id}/panel").text
    assert "Aguardando o resultado oficial" not in panel, "no banner after the release"
    assert "renderizar agora: espera pelo resultado encerrada" in panel, "the log names it"
    assert ask_next(harness, master, [TaskKind.PROCESS])["job_id"] == job_id


def test_the_settings_page_holds_the_number_of_minutes(admin, harness):
    # Arrange: the admin opens Configurações (the value starts at 15).
    page = admin.get("/review/admin/settings").text
    assert "Esperar o resultado oficial por até (minutos)" in page, "the field is labelled"
    assert 'name="result_wait_minutes" type="number" step="1" min="1" max="120"' in page

    # Act: the admin saves 30 minutes.
    follow(
        admin,
        post_form(
            admin,
            "/review/admin/settings",
            intake_mode="open",
            inbox_hold_gb="22",
            proxy_evict_min_free_gb="10",
            installer_download_url="",
            result_wait_minutes="30",
        ),
    )

    # Assert: stored by the orchestrator.
    assert harness.context.admin_settings.current.result_wait_minutes == 30, "saved as typed"
