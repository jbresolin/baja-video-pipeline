"""Tests that every Jinja template renders with representative data.

These render the templates directly (no web request), with the same
context the routes build, so a typo in a template or a renamed field fails
here with a clear message instead of only when someone opens the page.
"""

from dataclasses import replace

import pytest

from review_ui.clip_inputs_view import outdated_clip
from review_ui.clips_and_fleet_view import build_worker_rows, jobs_by_state_in_order, online_count
from review_ui.event_log_view import event_lines_newest_first
from review_ui.job_view import (
    action_buttons,
    explain_status,
    frame_rate_for_stepping,
    intake_text,
)
from review_ui.media_info_view import media_panel
from review_ui.pass_clock_view import overlay_preview_data, pass_clock_box
from review_ui.pass_result_view import pass_result_box
from review_ui.passagem_help_view import passagem_help
from review_ui.queue_view import STATES_IN_PIPELINE_ORDER, QueueFilter
from review_ui.render_options_view import render_options_box
from review_ui.sender_messages_view import sender_messages_section
from review_ui.settings import ReviewUiSettings
from review_ui.templating import TEMPLATES_DIR, build_templates
from shared.models import CurrentUser, JobState, JobSummary, SenderMessagesResponse
from tests.review_ui.sample_data import (
    T0,
    bruno_messages,
    car_7_video,
    delivered_job,
    edited_after_delivery_job,
    failed_job,
    failing_fetch,
    make_fleet,
    make_job,
    make_manifest,
    on_event_day,
    pass_clock_job,
    pass_result,
    pass_result_job,
)
from tests.review_ui.sample_events import make_events
from tests.review_ui.test_media_panel import IPHONE_REPORT

SETTINGS = ReviewUiSettings(public_base_url="https://baja.example.org")
NAMES = {"w-master-0001": "joao-desktop"}


def common_context() -> dict:
    """What render_page adds to every page."""
    user = CurrentUser(username="ana", role="reviewer")
    return {"user": user, "is_admin": False, "settings": SETTINGS, "now": T0, "active_nav": "queue"}


def job_context(job) -> dict:
    job_events = [event for event in make_events() if event.job_id == job.id]
    return {
        "job": job,
        "notes": explain_status(job, NAMES),
        "event_lines": event_lines_newest_first(job_events, NAMES),
        "intake": intake_text(job),
        "frame_rate": frame_rate_for_stepping(job),
        "outdated": outdated_clip(job.clip_inputs, job.state),
        "actions": action_buttons(job.state),
        "prova_choices": ["Enduro", "Suspensão"],
        "poll_seconds": 5,
        "media": media_panel(job),
        # "Imagem e som" (LIVESTACK-50), as routes_job.job_page adds it.
        "render_box": render_options_box(job),
        # "Mostrar resultado da passagem" (LIVESTACK-47); None for most sample jobs.
        "pass_box": pass_result_box(job.pass_result, T0),
        # "Cronômetro" and the overlay preview (LIVESTACK-48), as job_page adds them.
        "clock_box": pass_clock_box(
            job.pass_clock, T0, result_box_shown=job.pass_result is not None
        ),
        "preview": overlay_preview_data(job),
    }


def passagem_help_context() -> dict:
    """Car 7 in Suspensão: passes 1 and 2 numbered, this video filmed after both."""
    this_video = car_7_video("job-this", on_event_day(14, 50))
    others = [
        car_7_video("job-p1", on_event_day(14, 2), passagem=1),
        car_7_video("job-p2", on_event_day(14, 31), passagem=2),
    ]
    summaries = [JobSummary.from_job(other) for other in others]
    panel = passagem_help(this_video, "Suspensão", 7, summaries, 3)
    return {**job_context(this_video), "help": panel}


def queue_context() -> dict:
    jobs = [make_job(), failed_job(), make_job("job-low", low_res=True, proxy_available=False)]
    return {
        "items": [JobSummary.from_job(job) for job in jobs],
        "prova_options": ["Enduro"],
        "filter": QueueFilter(),
        "all_states": STATES_IN_PIPELINE_ORDER,
        "poll_seconds": 5,
    }


def clips_context() -> dict:
    manifest = make_manifest()
    return {
        "rows": [
            (clip, clip.relative_path, outdated_clip(clip.clip_inputs)) for clip in manifest.clips
        ],
        "prova_options": ["Enduro", "Suspensão"],
        "chosen_prova": "",
        "total_count": 2,
        "generated_at": manifest.generated_at,
    }


def fleet_context() -> dict:
    fleet = make_fleet()
    events = make_events()
    by_worker = {
        entry.worker.id: [event for event in events if event.worker_id == entry.worker.id]
        for entry in fleet.workers
    }
    return {
        "fleet": fleet,
        "worker_rows": build_worker_rows(fleet, by_worker),
        "online_count": online_count(fleet),
        "job_counts": jobs_by_state_in_order(fleet),
        "poll_seconds": 10,
    }


# (template name, function building its context). Every template file in
# the folder must appear here; the last test checks that none is forgotten.
def sender_messages_context() -> dict:
    job = make_job()
    response = SenderMessagesResponse(job_id=job.id, sender_name="Bruno", items=bruno_messages())
    return {**job_context(job), "messages": sender_messages_section(response, job)}


CASES = {
    "queue.html": queue_context,
    "_queue_rows.html": queue_context,
    "job.html": lambda: job_context(make_job()),
    "_passagem_help.html": passagem_help_context,
    "_sender_messages.html": sender_messages_context,
    "_job_panel.html": lambda: job_context(failed_job()),
    # A clip edited after delivery: the status strip carries the warning banner.
    "_job_status.html": lambda: job_context(edited_after_delivery_job()),
    "_job_events.html": lambda: job_context(delivered_job()),
    "_media_info.html": lambda: job_context(make_job(media_info=IPHONE_REPORT)),
    # A phone video filmed upright, "Cortar" chosen: the whole box shows.
    "_render_options.html": lambda: job_context(
        make_job(display_width=1080, display_height=1920, vertical=True, framing="crop")
    ),
    # A result shown, the results API failing since a while (LIVESTACK-47).
    "_pass_result.html": lambda: job_context(
        pass_result_job(pass_result=pass_result(fetch=failing_fetch()))
    ),
    # The running clock: largada marked, official time known (LIVESTACK-48).
    "_pass_clock.html": lambda: job_context(pass_clock_job()),
    "clips.html": clips_context,
    "fleet.html": fleet_context,
    "_fleet_body.html": fleet_context,
    "login.html": lambda: {"next_path": "/review/", "error": "Usuário ou senha incorretos."},
    "error.html": lambda: {
        "title": "Não encontrado",
        "status_code": 404,
        "code": "not_found",
        "explanation": "Não encontrado (talvez alguém removeu agora).",
        "detail": "no job x",
    },
}


def render(name: str, context: dict) -> str:
    templates = build_templates(SETTINGS)
    return templates.env.get_template(name).render({**common_context(), **context})


@pytest.mark.parametrize("name", sorted(CASES))
def test_template_renders(name):
    html = render(name, CASES[name]())

    # Rendering succeeded and produced real content.
    assert len(html) > 100


def test_every_template_file_has_a_render_case():
    # base.html and macros.html are only used through the others. The admin
    # pages (templates/admin/) are rendered by test_admin_pages.py, against
    # the real orchestrator.
    files = {path.name for path in TEMPLATES_DIR.glob("*.html")} - {"base.html", "macros.html"}

    assert files == set(CASES)


def test_media_links_use_the_public_base_url():
    html = render("job.html", job_context(make_job()))

    # With BVP_PUBLIC_BASE_URL set, the proxy link is absolute on that site.
    assert 'src="https://baja.example.org/proxies/job-awaiting-1.mp4"' in html


def test_text_from_senders_and_workers_is_escaped():
    # A sender name and a worker error message are text from outside; they
    # must never become HTML. "<script>" has to come out as "&lt;script&gt;".
    job = failed_job()
    job = job.model_copy(
        update={
            "source": job.source.model_copy(update={"sender_name": "<script>alert(1)</script>"}),
            "failure": job.failure.model_copy(update={"message": "<b>boom</b>"}),
        }
    )

    html = render("job.html", job_context(job))

    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "<b>boom</b>" not in html


def test_discarded_job_offers_only_restore():
    html = render("job.html", job_context(make_job(state=JobState.DISCARDED)))

    # Restore is the one action left (LIVESTACK-38: a discard can be undone).
    assert 'data-action="restore"' in html
    assert html.count("data-action=") == 1


def test_car_number_zero_is_shown_not_treated_as_missing():
    # 0 is a valid car number; a careless "if car_number" would hide it.
    job = make_job(prova="Enduro", car_number=0, passagem=1)
    html = render("_queue_rows.html", {**queue_context(), "items": [JobSummary.from_job(job)]})

    assert "carro 0 · passagem 1" in html


# --- Event log and frame rate ---


def test_job_event_log_shows_actor_change_and_reason():
    html = render("_job_events.html", job_context(delivered_job()))

    # One line per event: who (bold), what (pt-BR state labels), and the
    # orchestrator's reason, shown as it came and marked as English.
    assert '<strong class="actor">ana</strong> aguardando revisão → pronto' in html
    assert "joao-desktop</strong> recebido → proxy pronto" in html
    assert '<span lang="en" class="reason">proxy uploaded</span>' in html


def test_job_event_log_says_when_it_could_not_be_loaded():
    # event_lines is None when GET /jobs/{id}/events failed.
    html = render("_job_events.html", {**job_context(make_job()), "event_lines": None})

    assert "Histórico indisponível" in html
    assert "<ol>" not in html


def test_job_event_log_says_when_there_are_no_events_yet():
    # An empty list (the call worked, nothing happened yet) is not a failure.
    html = render("_job_events.html", {**job_context(make_job()), "event_lines": []})

    assert "Nenhum evento registrado para este vídeo ainda." in html
    assert "indisponível" not in html


def test_event_reasons_are_escaped():
    # A reason can carry a worker's error text; it must stay text, not HTML.
    context = job_context(failed_job())
    # replace() copies the first line with only its reason changed.
    context["event_lines"] = [replace(context["event_lines"][0], reason="<b>x</b>")]

    html = render("_job_events.html", context)

    assert "<b>x</b>" not in html
    assert "&lt;b&gt;x&lt;/b&gt;" in html


def test_job_page_carries_the_frame_rate_for_the_player():
    # The player script reads data-frame-rate and steps by 1/29.97 s.
    html = render("job.html", job_context(make_job(frame_rate=29.97)))

    assert 'data-frame-rate="29.97"' in html
    # The shortcut list says which rate a frame step uses.
    # (with a decimal comma: shown to people, unlike the data attribute)
    assert "um quadro para trás / para a frente (29,97 fps)" in html


def test_job_page_frame_rate_falls_back_to_30():
    # No frame rate on the job (proxy not measured): 30 fps is used.
    html = render("job.html", job_context(make_job()))

    assert 'data-frame-rate="30.0"' in html
    assert "um quadro para trás / para a frente (30 fps)" in html


def test_fleet_rows_show_recent_events_or_that_they_are_unavailable():
    context = fleet_context()
    master_row, volunteer_row = context["worker_rows"]
    # Setup: pretend loading the volunteer's events failed.
    context["worker_rows"] = [master_row, replace(volunteer_row, recent_events=None)]

    html = render("_fleet_body.html", context)

    # The master's own events do not repeat its name as the actor, but an
    # event by someone else (the admin who enrolled it) names them...
    master_events = html[html.index(f'data-worker-id="{master_row.entry.worker.id}"') :]
    master_events = master_events[: master_events.index("</tr>")]
    assert "em processamento → enviado" in master_events
    assert '<strong class="actor">joao-desktop</strong>' not in master_events
    assert '<strong class="actor">token de admin</strong>' in master_events
    # ...and each event links to its job.
    assert '<a href="/review/jobs/job-done-1"><code>job-done</code></a>' in master_events
    # The volunteer's row says its events could not be loaded.
    assert "Eventos recentes indisponíveis." in html
