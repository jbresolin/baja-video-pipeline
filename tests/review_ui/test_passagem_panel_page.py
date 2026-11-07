"""The queue in filming order and the job page's passagem panel, through the UI (LIVESTACK-45).

Setup: a fake orchestrator (fake_orchestrator.py) holding videos of Baja
UFPR's car 7 in "Suspensão", filmed on 2026-03-26, and a signed-in test
browser for the real review UI. The panel's rules themselves are tested in
test_passagem_help_view.py; here we check what reaches the page:

* the queue lists videos oldest recording first, with the recording time
  and a marker when it is only the WhatsApp send time;
* the job page shows "Outros vídeos de Suspensão, carro 7" for the saved
  values, with the suggestion and the warnings;
* GET /review/api/jobs/{id}/passagem-help gives the same panel for values
  typed but not saved, as JSON (the page swaps it in, no reload).
"""

import re

import pytest

from shared.models import JobState, RecordedAtSource
from tests.review_ui.conftest import build_ui
from tests.review_ui.fake_orchestrator import FakeOrchestrator
from tests.review_ui.sample_data import SIGNED_IN_COOKIES, car_7_video, make_job, on_event_day


@pytest.fixture
def fake_orchestrator() -> FakeOrchestrator:
    """Passes 1 (14:02) and 2 (14:31) are numbered; job-this (14:50) is being reviewed."""
    fake = FakeOrchestrator()
    fake.add_job(car_7_video("job-p1", on_event_day(14, 2), passagem=1, state=JobState.DONE))
    fake.add_job(car_7_video("job-p2", on_event_day(14, 31), passagem=2, state=JobState.READY))
    fake.add_job(car_7_video("job-this", on_event_day(14, 50)))
    return fake


@pytest.fixture
def ui(fake_orchestrator, settings):
    return build_ui(fake_orchestrator, settings, SIGNED_IN_COOKIES)


def panel_of(html: str) -> str:
    """The inside of <section id="passagem-help">…</section>."""
    match = re.search(r'<section class="passagem-help" id="passagem-help".*?</section>', html, re.S)
    assert match, "the job page has no passagem panel"
    return match.group(0)


def passagem_help(ui, job_id="job-this", **form_values):
    response = ui.get(f"/review/api/jobs/{job_id}/passagem-help", params=form_values)
    assert response.status_code == 200, response.text
    return response.json()


# --- The queue ---


def test_the_queue_lists_videos_oldest_recording_first(ui, fake_orchestrator):
    # Three videos that arrived in the reverse of their filming order: the
    # 14:31 one arrived first (14:40), the 14:02 one last (14:42).
    fake_orchestrator.jobs.clear()
    for job_id, filmed, arrived in [
        ("job-late", on_event_day(14, 31), on_event_day(14, 40)),
        ("job-middle", on_event_day(14, 15), on_event_day(14, 41)),
        ("job-early", on_event_day(14, 2), on_event_day(14, 42)),
    ]:
        job = car_7_video(job_id, filmed).model_copy(update={"created_at": arrived})
        fake_orchestrator.add_job(job)

    html = ui.get("/review/").text

    # The page keeps the orchestrator's order: filming order.
    positions = [
        html.index(f'href="/review/jobs/{job_id}"')
        for job_id in ("job-early", "job-middle", "job-late")
    ]
    assert positions == sorted(positions)


def test_a_queue_row_shows_the_recording_time_and_marks_a_send_time(ui, fake_orchestrator):
    fake_orchestrator.jobs.clear()
    fake_orchestrator.add_job(car_7_video("job-exact", on_event_day(14, 2)))
    fake_orchestrator.add_job(
        car_7_video("job-sent", on_event_day(14, 31), source=RecordedAtSource.WHATSAPP)
    )

    html = ui.get("/review/").text

    # Not today (the test runs after the event), so the date is shown, in
    # São Paulo time.
    assert ">26/03/2026 14:02</time>" in html
    assert ">26/03/2026 14:31</time>" in html
    # Only the send time gets the "envio" marker, explained on hover.
    assert html.count('class="time-hint"') == 1
    assert ">envio</span>" in html


# --- The job page ---


def test_the_job_page_shows_the_cars_other_videos_and_the_suggestion(ui, fake_orchestrator):
    html = ui.get("/review/jobs/job-this").text

    panel = panel_of(html)
    assert "Outros vídeos de Suspensão, carro 7" in panel
    # Each other video links to its page; this one is marked, not linked.
    assert 'href="/review/jobs/job-p1"' in panel
    assert 'href="/review/jobs/job-p2"' in panel
    assert "este vídeo" in panel
    assert "Passagens já registradas: 1 (14:02), 2 (14:31)" in panel
    assert "Sugestão: passagem 3 — gravado depois da passagem 2, de 14:31" in panel
    # The suggestion is a button: the field is filled only on a click.
    assert 'data-use-passagem="3"' in panel
    assert re.search(r'id="passagem"[^>]*value=""', html)
    # The UI asked the orchestrator by prova and car.
    [call] = fake_orchestrator.requests_to("GET", "/review/car-videos")
    assert dict(call.query) == {"prova": "Suspensão", "car_number": "7"}


def test_the_job_page_shows_the_recording_time_and_its_source(ui):
    html = ui.get("/review/jobs/job-this").text
    assert 'id="recorded-at"' in html
    assert "(horário do arquivo)" in html


def test_a_saved_duplicate_number_is_warned_next_to_the_field_and_on_ready(ui, fake_orchestrator):
    fake_orchestrator.add_job(car_7_video("job-this", on_event_day(14, 50), passagem=2))

    html = ui.get("/review/jobs/job-this").text

    warning = "A passagem 2 já é o vídeo de 14:31."
    # Once beside the passagem field, once in the box next to "Pronto"...
    assert html.count(warning) == 2
    # ...and that box is visible (not hidden) because there is a warning.
    assert re.search(r'id="ready-warnings"\s*>', html)
    # A warning never removes the button: re-runs are real.
    assert 'data-action="ready"' in html


def test_without_prova_or_car_there_is_no_panel_and_no_call(ui, fake_orchestrator):
    fake_orchestrator.add_job(make_job("job-new"))  # no prova, no car

    html = ui.get("/review/jobs/job-new").text

    assert "<h2>Outros vídeos de" not in html
    assert fake_orchestrator.requests_to("GET", "/review/car-videos") == []
    # The warnings box exists for the script, but hidden.
    assert re.search(r'id="ready-warnings" hidden>', html)


def test_the_page_still_works_when_the_list_cannot_be_read(ui, fake_orchestrator):
    # The panel helps; it must never stop a reviewer from working.
    fake_orchestrator.car_videos_fail = True

    response = ui.get("/review/jobs/job-this")

    assert response.status_code == 200
    assert "<h2>Outros vídeos de" not in response.text
    # The panel's place is there, so the script can fill it on the next change.
    assert 'id="passagem-help"' in response.text


def test_the_json_endpoint_reports_a_failed_list_as_a_json_error(ui, fake_orchestrator):
    fake_orchestrator.car_videos_fail = True
    response = ui.get(
        "/review/api/jobs/job-this/passagem-help", params={"prova": "Suspensão", "car_number": 7}
    )
    # A pt-BR JSON error the script shows inside the panel.
    assert response.status_code >= 500
    assert response.json()["text"]


# --- The JSON endpoint the page script calls while the reviewer types ---


def test_typed_values_give_the_panel_and_warnings_as_json(ui):
    # Not saved: the reviewer typed car 7 and passagem 2 on job-this.
    answer = passagem_help(ui, prova="Suspensão", car_number=7, passagem=2)

    assert "Outros vídeos de Suspensão, carro 7" in answer["html"]
    assert answer["warnings"] == ["A passagem 2 já é o vídeo de 14:31."]
    assert answer["suggestion"] == 3


def test_typing_another_car_shows_that_cars_videos(ui, fake_orchestrator):
    car_8 = car_7_video("job-car-8", on_event_day(14, 5)).model_copy(update={"car_number": 8})
    fake_orchestrator.add_job(car_8)

    answer = passagem_help(ui, prova="Suspensão", car_number=8)

    assert "Outros vídeos de Suspensão, carro 8" in answer["html"]
    assert 'href="/review/jobs/job-car-8"' in answer["html"]
    assert "job-p1" not in answer["html"]


def test_a_discarded_sibling_does_not_count(ui, fake_orchestrator):
    # Passagem 2's video was thrown away: 2 is free again, and the 14:50
    # video should be passagem 2.
    fake_orchestrator.jobs["job-p2"] = fake_orchestrator.jobs["job-p2"].model_copy(
        update={"state": JobState.DISCARDED}
    )

    answer = passagem_help(ui, prova="Suspensão", car_number=7, passagem=2)

    assert answer["warnings"] == []
    assert answer["suggestion"] == 2
    assert "job-p2" not in answer["html"]


def test_without_a_car_the_panel_is_empty(ui):
    answer = passagem_help(ui, prova="Suspensão")
    assert answer == {"html": answer["html"], "warnings": [], "suggestion": None}
    assert "<h2>Outros vídeos de" not in answer["html"]


def test_a_nonsense_passagem_is_refused_with_a_json_error(ui):
    response = ui.get(
        "/review/api/jobs/job-this/passagem-help",
        params={"prova": "Suspensão", "car_number": 7, "passagem": 0},
    )
    # The script only sends whole numbers from 1; anything else is a 400 in
    # the UI's JSON error shape.
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_error"
