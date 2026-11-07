"""Tests for the job page (GET /review/jobs/{id}) and its status panel fragment.

Setup (conftest.py): the fake orchestrator holds "job-awaiting-1" (waiting
for review), "job-failed-1" (a worker failed rendering it) and "job-done-1"
(delivered). The fleet knows the master as "joao-desktop" and the volunteer
as "maria-notebook".
"""

from review_ui.job_view import NO_HOLDER_ONLINE_REASON
from shared.models import JobState
from tests.review_ui.sample_data import make_job


def test_player_uses_the_proxy_served_by_caddy(ui):
    html = ui.get("/review/jobs/job-awaiting-1").text

    # The <video> points at Caddy's static /proxies/ path (Range-served),
    # so no app process is in the video's byte path (design §6).
    assert '<video id="player" src="/proxies/job-awaiting-1.mp4"' in html
    assert 'poster="/thumbs/job-awaiting-1.jpg"' in html


def test_page_carries_the_version_the_reviewer_is_looking_at(ui):
    html = ui.get("/review/jobs/job-awaiting-1").text

    # The page script sends this number back as expected_version, which is
    # how the orchestrator detects that someone else saved in between.
    assert 'data-version="3"' in html


def test_existing_fields_and_cuts_are_filled_in(ui):
    html = ui.get("/review/jobs/job-failed-1").text

    # The prova select has the job's prova chosen.
    assert "<option selected>Enduro</option>" in html
    # Cuts are shown with millisecond precision.
    assert 'value="3.000"' in html
    assert 'value="40.000"' in html
    assert 'name="car_number"' in html and 'value="42"' in html


def test_empty_cut_fields_say_the_whole_video_is_used(ui):
    # Arrange: "job-awaiting-1" has no cut points yet (nobody marked any).

    # Act: open its job page.
    html = ui.get("/review/jobs/job-awaiting-1").text

    # Assert: the empty fields hint that "Pronto" without cuts keeps the
    # whole video, so the reviewer knows marking cuts is optional.
    assert 'placeholder="início do vídeo"' in html, "an empty cut-in means from the start"
    assert 'placeholder="fim do vídeo"' in html, "an empty cut-out means to the end"


def test_only_actions_allowed_in_the_state_are_offered(ui):
    # Awaiting review: save, ready, discard (docs/api.md §7.1).
    awaiting = ui.get("/review/jobs/job-awaiting-1").text
    assert 'data-action="save"' in awaiting
    assert 'data-action="ready"' in awaiting
    assert 'data-action="discard"' in awaiting
    assert 'data-action="retry"' not in awaiting
    assert 'data-action="reprocess"' not in awaiting

    # Failed: retry or discard (plus save).
    failed = ui.get("/review/jobs/job-failed-1").text
    assert 'data-action="retry"' in failed
    assert 'data-action="discard"' in failed
    assert 'data-action="ready"' not in failed

    # Done: only reprocess (plus save); a delivered clip cannot be discarded.
    done = ui.get("/review/jobs/job-done-1").text
    assert 'data-action="reprocess"' in done
    assert 'data-action="discard"' not in done


def test_status_strip_explains_a_failure_with_the_worker_name(ui):
    html = ui.get("/review/jobs/job-failed-1").text

    # The worker id in Job.failure is turned into the name from the fleet.
    assert "maria-notebook falhou ao renderizar o clipe (código de saída 1):" in html
    # The worker's own message follows, marked as English.
    assert '<span lang="en">ffmpeg exited: moov atom not found</span>' in html
    assert "Escolha “Tentar de novo” ou “Descartar”" in html


def test_status_strip_repeats_the_orchestrators_waiting_reason(ui, fake_orchestrator):
    fake_orchestrator.add_job(
        make_job("job-stuck", JobState.READY, waiting_reason="no holder online for 12 min")
    )

    html = ui.get("/review/jobs/job-stuck").text

    # A reason the UI has no words for is shown as it came, marked English.
    assert '<span lang="en">no holder online for 12 min</span>' in html
    assert "Esperando um worker com o original pegar o processamento" in html


def test_the_orchestrators_known_waiting_reasons_are_shown_in_pt_br(ui, fake_orchestrator):
    # Setup: the two reasons the orchestrator writes itself (no holder
    # online; a task failed on one worker and the job carries on).
    fake_orchestrator.add_job(
        make_job("job-no-holder", JobState.READY, waiting_reason=NO_HOLDER_ONLINE_REASON)
    )
    fake_orchestrator.add_job(
        make_job(
            "job-retrying",
            JobState.READY,
            waiting_reason="process failed on maria-notebook: ffmpeg exited",
        )
    )

    no_holder = ui.get("/review/jobs/job-no-holder").text
    retrying = ui.get("/review/jobs/job-retrying").text

    # The whole first sentence is translated; nothing English is left.
    assert "Esperando um worker com o original voltar a ficar online" in no_holder
    assert NO_HOLDER_ONLINE_REASON not in no_holder
    # The second: the task and worker in pt-BR, the worker's message as it came.
    assert "Falhou ao renderizar o clipe em maria-notebook:" in retrying
    assert '<span lang="en">ffmpeg exited</span>' in retrying


def test_missing_proxy_offers_to_request_one(ui, fake_orchestrator):
    fake_orchestrator.add_job(make_job("job-evicted", proxy_available=False, proxy_url=None))

    html = ui.get("/review/jobs/job-evicted").text

    # No <video>; instead a button that calls regenerate-proxy.
    assert "<video" not in html
    assert "data-regenerate-proxy" in html
    assert "O proxy não está no servidor" in html


def prova_options(html: str) -> list[str]:
    """The texts of the <option>s of the prova select, in order."""
    select = html[html.index('<select id="prova"') :]
    select = select[: select.index("</select>")]
    return [part.split(">", 1)[1].split("<", 1)[0] for part in select.split("<option")[1:]]


def test_the_prova_is_picked_from_the_active_provas_in_the_admins_order(ui, fake_orchestrator):
    html = ui.get("/review/jobs/job-awaiting-1").text

    # The list comes from GET /provas. "Lama" is deactivated, so it is not
    # offered; the first option is the empty "choose" one.
    assert fake_orchestrator.requests_to("GET", "/provas")
    assert prova_options(html) == ["Escolha uma prova…", "Enduro", "Suspensão"]


def test_a_jobs_prova_that_is_no_longer_active_is_still_shown(ui, fake_orchestrator):
    # The job was reviewed with "Lama" before the admin deactivated it:
    # opening the page must not silently change the job's prova.
    job = fake_orchestrator.jobs["job-awaiting-1"]
    fake_orchestrator.jobs["job-awaiting-1"] = job.model_copy(update={"prova": "Lama"})

    html = ui.get("/review/jobs/job-awaiting-1").text

    assert prova_options(html)[-1] == "Lama"
    assert "<option selected>Lama</option>" in html


def test_event_log_comes_from_the_orchestrators_log_newest_first(ui, fake_orchestrator):
    html = ui.get("/review/jobs/job-done-1").text
    log = html[html.index('id="event-log"') :]

    # The page asked the orchestrator for this job's log.
    assert fake_orchestrator.requests_to("GET", "/jobs/job-done-1/events")
    # Each line is one Event from sample_events.make_events(), in words.
    done = log.index("entregue → concluído")  # T0+21 min, by the system
    reviewed = log.index('<strong class="actor">ana</strong> aguardando revisão → pronto')  # T0+10
    confirmed = log.index("joao-desktop</strong> confirmou sua cópia do original")  # T0+2
    created = log.index("WhatsApp</strong> criado como recebido")  # T0
    # Newest first.
    assert done < reviewed < confirmed < created
    # The orchestrator's reason is shown with the line.
    assert "WhatsApp document from Bruno" in log


def test_event_log_shows_worker_names_and_failure_reasons(ui):
    html = ui.get("/review/jobs/job-failed-1").text
    log = html[html.index('id="event-log"') :]

    # "worker:w-volunteer-0002" is shown as the name from the fleet.
    assert "maria-notebook</strong> tarefa falhou" in log
    assert "ffmpeg exited: moov atom not found" in log
    # Only this job's events: the volunteer's lease on another job is not here.
    assert "tarefa expirou" not in log


def test_event_log_failure_does_not_break_the_page(ui, fake_orchestrator):
    # Setup: the event endpoints answer 500.
    fake_orchestrator.events_fail = True

    page = ui.get("/review/jobs/job-awaiting-1")
    panel = ui.get("/review/jobs/job-awaiting-1/panel")

    # The page and its refresh still load (200, not a 502 error page): the
    # player and the form are what the reviewer needs, the log is extra.
    assert page.status_code == 200
    assert 'id="review-form"' in page.text
    assert "Histórico indisponível" in page.text
    # The refresh says so too, and the status strip is still fresh.
    assert panel.status_code == 200
    assert "Histórico indisponível" in panel.text
    assert 'id="status-strip"' in panel.text


def test_page_head_says_who_sent_the_raw_and_how(ui):
    html = ui.get("/review/jobs/job-awaiting-1").text

    assert "Enviado por Bruno, como documento do WhatsApp (VID_0042.mp4)." in html


def test_frame_step_uses_the_jobs_frame_rate(ui, fake_orchestrator):
    # Setup: a holder measured this job's proxy at 29.97 fps.
    fake_orchestrator.add_job(make_job("job-ntsc", frame_rate=29.97))

    html = ui.get("/review/jobs/job-ntsc").text

    # The player script reads this attribute: one frame = 1/29.97 s.
    assert 'data-frame-rate="29.97"' in html


def test_frame_step_falls_back_to_30_fps(ui):
    # The sample job has no frame_rate (None), like a job from before the
    # proxy upload reported one.
    html = ui.get("/review/jobs/job-awaiting-1").text

    assert 'data-frame-rate="30.0"' in html


def test_page_still_works_when_the_fleet_cannot_be_read(ui, fake_orchestrator):
    # The fleet is only used to turn worker ids into names. If it fails,
    # the page must still load and fall back to short ids.
    fake_orchestrator.fleet_fails = True

    response = ui.get("/review/jobs/job-failed-1")

    assert response.status_code == 200
    assert "worker w-volunt falhou ao renderizar o clipe" in response.text


def test_unknown_job_shows_a_404_page(ui):
    response = ui.get("/review/jobs/does-not-exist")

    # The orchestrator's 404 is passed on, rendered as a readable page: the
    # code in pt-BR words, and the orchestrator's own sentence as details.
    assert response.status_code == 404
    assert "<h1>Não encontrado</h1>" in response.text
    assert "does-not-exist" in response.text


def test_status_panel_fragment_has_strip_and_log_with_current_version(ui, fake_orchestrator):
    response = ui.get("/review/jobs/job-failed-1/panel")

    assert response.status_code == 200
    assert "<html" not in response.text
    assert 'id="status-strip"' in response.text
    assert 'id="event-log"' in response.text
    # The page compares this version with its own to notice changes by others.
    assert 'data-version="3"' in response.text


def test_the_job_page_offers_baixar_original(ui):
    # LIVESTACK-41 item 15: for when the 480p proxy is not enough.
    html = ui.get("/review/jobs/job-awaiting-1").text
    assert "data-original" in html and "Baixar original" in html
    assert 'id="original-status"' in html
