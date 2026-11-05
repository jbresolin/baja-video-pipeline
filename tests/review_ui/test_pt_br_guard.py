"""Guard: no English UI text left anywhere in the review UI (LIVESTACK-39).

The reviewers are Brazilian volunteers, so every page, partial and page
script speaks pt-BR. These tests render every page and partial with
representative data (jobs in every state, failures, discards, a fleet, the
admin pages with real records) and fail if what a person reads contains a
common English UI word. english_guard.py says exactly what is read and what
is left out (code, key names, and the orchestrator's own English words,
which the pages mark with lang="en").
"""

from datetime import timedelta
from pathlib import Path

import pytest

from orchestrator.testing.scenarios import ensure_prova, send_video
from review_ui.job_view import NO_HOLDER_ONLINE_REASON
from review_ui.templating import STATIC_DIR
from shared.models import JobState, Lease, TaskKind, WorkerRole
from shared.testing.credentials import PLACEHOLDER_WRONG_PASSWORD
from tests.review_ui.admin_browser import follow, post_form
from tests.review_ui.english_guard import english_in_js, english_words_in, visible_text
from tests.review_ui.sample_data import MASTER_ID, T0, make_job
from tests.review_ui.test_admin_pages_catalog import set_event
from tests.review_ui.test_login import sign_in
from tests.review_ui.test_media_panel import IPHONE_REPORT
from tests.review_ui.test_restore import discarded_job
from tests.review_ui.test_templates_render import CASES, job_context, render


def assert_pt_br(html: str, where: str) -> None:
    """Fail with the English words found, and where, so the fix is obvious."""
    found = english_words_in(visible_text(html))
    assert not found, f"English UI words on {where}: {sorted(found)}"


# --- The guard itself (so a passing guard means something) ---


def test_the_guard_finds_english_in_text_and_shown_attributes():
    html = '<p title="Save it">Pronto</p><button aria-label="Reload">↻</button>'

    assert english_words_in(visible_text(html)) == {"save", "it", "reload"}


def test_the_guard_skips_code_and_the_orchestrators_english_words():
    # Ids and codes in <code>/<kbd>, and English marked lang="en" (a worker's
    # failure message), are data, not UI text.
    html = (
        "<p>Falhou <code>job_failed</code> <kbd>Enter</kbd>"
        '<span lang="en">the job failed: <b>ffmpeg</b> exited</span></p>'
    )

    assert english_words_in(visible_text(html)) == set()


def test_the_js_guard_finds_sentences_but_not_code():
    source = """
      // A comment in English is fine.
      const a = "Saved.";
      const b = `Copy failed (${status})`;
      const c = "ready";                 // an API action value
      const d = "/review/api/jobs/x";    // a URL path
      const e = t("script.copy");        // a catalog key
    """

    assert english_in_js(source) == {"Saved.": {"saved"}, "Copy failed ( )": {"copy", "failed"}}


# --- Reviewer pages, through the app (fake orchestrator) ---


@pytest.fixture
def every_kind_of_job(fake_orchestrator):
    """Besides the default three jobs: one per state, and the special cases."""
    for state in JobState:
        fake_orchestrator.add_job(make_job(f"job-{state.value}", state, prova="Enduro"))
    lease = Lease(
        lease_id="l1",
        job_id="job-claimed-2",
        worker_id=MASTER_ID,
        task_kind=TaskKind.PROCESS,
        acquired_at=T0,
        expires_at=T0 + timedelta(seconds=45),
    )
    extra = [
        make_job("job-lowres", low_res=True, proxy_available=False, proxy_url=None),
        make_job("job-media", media_info=IPHONE_REPORT, frame_rate=29.97),
        make_job("job-claimed-2", JobState.CLAIMED, leases=[lease]),
        make_job("job-stuck", JobState.READY, waiting_reason=NO_HOLDER_ONLINE_REASON),
        make_job("job-retrying", JobState.READY, waiting_reason="process failed on pc: boom"),
        discarded_job("job-discarded-grace"),
        discarded_job("job-discarded-over", discard_grace_ends_at=T0 - timedelta(days=1)),
    ]
    for job in extra:
        fake_orchestrator.add_job(job)
    return fake_orchestrator


def reviewer_paths(fake_orchestrator) -> list[str]:
    every_state = "&".join(f"state={state.value}" for state in JobState)
    paths = [
        "/review/",
        f"/review/?{every_state}",
        "/review/?state=discarded&flagged=1&prova=Enduro",
        f"/review/partials/queue?{every_state}",
        "/review/clips",
        "/review/clips?prova=Nada",
        "/review/fleet",
        "/review/partials/fleet",
        "/review/jobs/does-not-exist",  # the 404 error page
    ]
    for job_id in fake_orchestrator.jobs:
        paths += [f"/review/jobs/{job_id}", f"/review/jobs/{job_id}/panel"]
    return paths


def test_every_reviewer_page_and_partial_is_pt_br(ui, every_kind_of_job):
    paths = reviewer_paths(every_kind_of_job)

    for path in paths:
        assert_pt_br(ui.get(path).text, path)
    # A sanity check that the loop really covered the job pages.
    assert len(paths) > 2 * len(JobState)


def test_the_pages_say_they_are_pt_br(ui, anonymous_ui):
    # <html lang="pt-BR"> on every page, admin or not, signed in or not.
    for html in (ui.get("/review/").text, anonymous_ui.get("/review/login").text):
        assert html.startswith('<!doctype html>\n<html lang="pt-BR">')


def test_the_login_page_and_its_errors_are_pt_br(anonymous_ui):
    assert_pt_br(anonymous_ui.get("/review/login").text, "/review/login")
    wrong = sign_in(anonymous_ui, password=PLACEHOLDER_WRONG_PASSWORD)
    assert wrong.status_code == 401
    assert_pt_br(wrong.text, "the failed login page")


def test_the_unreachable_orchestrator_page_is_pt_br(ui, fake_orchestrator):
    fake_orchestrator.fleet_fails = True
    # The fleet page needs the fleet: this is its error page.
    response = ui.get("/review/fleet")

    assert response.status_code >= 500
    assert_pt_br(response.text, "the fleet error page")


# --- Every template, rendered directly (test_templates_render.CASES) ---


@pytest.mark.parametrize("name", sorted(CASES))
def test_every_template_renders_pt_br(name):
    assert_pt_br(render(name, CASES[name]()), name)


@pytest.mark.parametrize("state", list(JobState))
def test_the_job_page_is_pt_br_in_every_state(state):
    html = render("job.html", job_context(make_job(f"job-{state.value}", state)))

    assert_pt_br(html, f"job.html ({state.value})")


# --- Admin pages, against the real orchestrator, with records on them ---


ADMIN_PAGES = ["users", "contacts", "provas", "teams", "enrollment", "workers", "settings"]


def test_every_admin_page_is_pt_br_with_records(admin, harness):
    # Setup: an event synced from the results site (provas and teams), a
    # manual prova, a contact who wrote, an enrollment token and a worker,
    # so every table has rows and every audit box has lines.
    follow(admin, set_event(admin))
    ensure_prova(harness, "Abertura")
    send_video(harness, sender_phone="5511987654321")
    harness.enroll(WorkerRole.WORKER, "pc-da-maria")
    created = post_form(
        admin,
        "/review/admin/enrollment/create",
        volunteer_name="Maria",
        role="worker",
        valid_for_hours="72",
    )
    assert_pt_br(created.text, "the page with a new token")

    for page in ADMIN_PAGES:
        assert_pt_br(admin.get(f"/review/admin/{page}").text, f"/review/admin/{page}")
    # A refused change shows the reason on the page: the code in words, then
    # the orchestrator's details (marked English).
    refused = post_form(admin, "/review/admin/provas/create", name="ABERTURA")
    assert refused.status_code == 409
    assert_pt_br(refused.text, "a refused admin change")


# --- Page scripts ---

SCRIPTS = sorted((STATIC_DIR / "js").glob("*.js"))


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda path: Path(path).name)
def test_page_scripts_have_no_english_strings(script):
    # Every sentence the scripts show comes from the catalog through t(...)
    # (js/text.js); a string literal with English words means one slipped in.
    found = english_in_js(script.read_text(encoding="utf-8"))

    assert found == {}, f"English strings in {script.name}: {found}"
