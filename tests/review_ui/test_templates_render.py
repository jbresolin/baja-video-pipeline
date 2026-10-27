"""Tests that every Jinja template renders with representative data.

These render the templates directly (no web request), with the same
context the routes build, so a typo in a template or a renamed field fails
here with a clear message instead of only when someone opens the page.
"""

from dataclasses import replace

import pytest

from review_ui.clips_and_fleet_view import build_worker_rows, jobs_by_state_in_order, online_count
from review_ui.event_log_view import event_lines_newest_first
from review_ui.job_view import (
    ACTION_LABELS,
    available_actions,
    explain_status,
    frame_rate_for_stepping,
    intake_text,
)
from review_ui.queue_view import STATES_IN_PIPELINE_ORDER, QueueFilter
from review_ui.settings import ReviewUiSettings
from review_ui.templating import TEMPLATES_DIR, build_templates
from shared.models import JobState, JobSummary
from tests.review_ui.sample_data import (
    T0,
    delivered_job,
    failed_job,
    make_fleet,
    make_job,
    make_manifest,
)
from tests.review_ui.sample_events import make_events

SETTINGS = ReviewUiSettings(public_base_url="https://baja.example.org")
NAMES = {"w-master-0001": "joao-desktop"}


def common_context() -> dict:
    """What render_page adds to every page."""
    return {"reviewer": "ana", "settings": SETTINGS, "now": T0, "active_nav": "queue"}


def job_context(job) -> dict:
    job_events = [event for event in make_events() if event.job_id == job.id]
    return {
        "job": job,
        "notes": explain_status(job, NAMES),
        "event_lines": event_lines_newest_first(job_events, NAMES),
        "intake": intake_text(job),
        "frame_rate": frame_rate_for_stepping(job),
        "actions": [
            (action.value, ACTION_LABELS[action]) for action in available_actions(job.state)
        ],
        "prova_suggestions": ["Enduro", "Suspensão"],
        "poll_seconds": 5,
    }


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
        "rows": [(clip, clip.relative_path) for clip in manifest.clips],
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
CASES = {
    "queue.html": queue_context,
    "_queue_rows.html": queue_context,
    "job.html": lambda: job_context(make_job()),
    "_job_panel.html": lambda: job_context(failed_job()),
    "_job_status.html": lambda: job_context(delivered_job()),
    "_job_events.html": lambda: job_context(delivered_job()),
    "clips.html": clips_context,
    "fleet.html": fleet_context,
    "_fleet_body.html": fleet_context,
    "error.html": lambda: {
        "title": "Not found",
        "status_code": 404,
        "code": "not_found",
        "message": "no job x",
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
    # base.html and macros.html are only used through the others.
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


def test_discarded_job_has_no_actions():
    html = render("job.html", job_context(make_job(state=JobState.DISCARDED)))

    assert "data-action=" not in html
    assert "No review actions in this state." in html


def test_car_number_zero_is_shown_not_treated_as_missing():
    # 0 is a valid car number; a careless "if car_number" would hide it.
    job = make_job(prova="Enduro", car_number=0, passagem=1)
    html = render("_queue_rows.html", {**queue_context(), "items": [JobSummary.from_job(job)]})

    assert "car 0 · pass 1" in html


# --- Event log and frame rate ---


def test_job_event_log_shows_actor_change_and_reason():
    html = render("_job_events.html", job_context(delivered_job()))

    # One line per event: who (bold), what, and the orchestrator's reason.
    assert '<strong class="actor">ana</strong> awaiting review → ready' in html
    assert "joao-desktop</strong> received → proxy ready" in html
    assert '<span class="reason">proxy uploaded</span>' in html


def test_job_event_log_says_when_it_could_not_be_loaded():
    # event_lines is None when GET /jobs/{id}/events failed.
    html = render("_job_events.html", {**job_context(make_job()), "event_lines": None})

    assert "Event log unavailable" in html
    assert "<ol>" not in html


def test_job_event_log_says_when_there_are_no_events_yet():
    # An empty list (the call worked, nothing happened yet) is not a failure.
    html = render("_job_events.html", {**job_context(make_job()), "event_lines": []})

    assert "No events recorded for this job yet." in html
    assert "unavailable" not in html


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
    assert "one frame back / forward (29.97 fps)" in html


def test_job_page_frame_rate_falls_back_to_30():
    # No frame rate on the job (proxy not measured): 30 fps is used.
    html = render("job.html", job_context(make_job()))

    assert 'data-frame-rate="30.0"' in html
    assert "one frame back / forward (30 fps)" in html


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
    assert "claimed → uploaded" in master_events
    assert '<strong class="actor">joao-desktop</strong>' not in master_events
    assert '<strong class="actor">admin</strong>' in master_events
    # ...and each event links to its job.
    assert '<a href="/review/jobs/job-done-1"><code>job-done</code></a>' in master_events
    # The volunteer's row says its events could not be loaded.
    assert "Recent events unavailable." in html
