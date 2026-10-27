"""Tests that every Jinja template renders with representative data.

These render the templates directly (no web request), with the same
context the routes build, so a typo in a template or a renamed field fails
here with a clear message instead of only when someone opens the page.
"""

import pytest

from review_ui.clips_and_fleet_view import build_worker_rows, jobs_by_state_in_order, online_count
from review_ui.job_view import ACTION_LABELS, available_actions, build_timeline, explain_status
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

SETTINGS = ReviewUiSettings(public_base_url="https://baja.example.org")
NAMES = {"w-master-0001": "joao-desktop"}


def common_context() -> dict:
    """What render_page adds to every page."""
    return {"reviewer": "ana", "settings": SETTINGS, "now": T0, "active_nav": "queue"}


def job_context(job) -> dict:
    return {
        "job": job,
        "notes": explain_status(job, NAMES),
        "timeline": build_timeline(job, NAMES),
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
    return {
        "fleet": fleet,
        "worker_rows": build_worker_rows(fleet),
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
