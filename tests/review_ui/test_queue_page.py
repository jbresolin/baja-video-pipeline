"""Tests for the queue page (GET /review/) and its auto-refresh fragment.

Setup for every test (see conftest.py): a fake orchestrator holding three
jobs: one awaiting review, one failed, one done. The `ui` fixture is a
signed-in browser talking to the real review UI app.
"""

from tests.review_ui.sample_data import make_job


def test_bare_review_path_redirects_to_the_queue(ui):
    # Caddy forwards both /review and /review/…; the UI's pages live under
    # /review/, so the bare path must send the browser there.
    response = ui.get("/review")

    assert response.status_code == 308
    assert response.headers["location"] == "/review/"


def test_default_queue_shows_jobs_awaiting_review_and_failed_jobs(ui, fake_orchestrator):
    response = ui.get("/review/")

    assert response.status_code == 200
    # The UI asked the orchestrator for exactly the two default states. The
    # repeated "state" parameter is how the API takes several states.
    queue_calls = fake_orchestrator.requests_to("GET", "/review/queue")
    states_sent = [value for name, value in queue_calls[0].query if name == "state"]
    assert states_sent == ["awaiting_review", "failed"]
    # Both matching jobs are listed as links to their job pages…
    assert 'href="/review/jobs/job-awaiting-1"' in response.text
    assert 'href="/review/jobs/job-failed-1"' in response.text
    # …but the done job is not, because its state was not asked for.
    assert "job-done-1" not in response.text


def test_queue_row_shows_thumbnail_sender_and_failure_message(ui):
    html = ui.get("/review/").text

    # The thumbnail comes from Caddy's /thumbs/ path given in the job.
    assert 'src="/thumbs/job-awaiting-1.jpg"' in html
    # The sender's WhatsApp name helps reviewers recognise who sent it.
    assert "Bruno" in html
    # A failed job shows the worker's error right in the list.
    assert "ffmpeg exited: moov atom not found" in html
    # The received time is a <time> element the browser turns into local time.
    assert '<time datetime="2026-10-25T14:00:00Z"' in html


def test_low_res_jobs_get_a_badge(ui, fake_orchestrator):
    # Setup: one more job, flagged low_res (it arrived as a WhatsApp video).
    fake_orchestrator.add_job(make_job("job-lowres-1", low_res=True))

    html = ui.get("/review/").text

    # Exactly one badge: the low_res job has it, the others do not.
    assert html.count(">low-res</span>") == 1


def test_state_filter_is_passed_to_the_orchestrator(ui, fake_orchestrator):
    response = ui.get("/review/?state=done")

    queue_calls = fake_orchestrator.requests_to("GET", "/review/queue")
    assert [value for name, value in queue_calls[0].query if name == "state"] == ["done"]
    # Now only the done job shows up.
    assert "job-done-1" in response.text
    assert "job-awaiting-1" not in response.text


def test_unknown_state_in_the_url_falls_back_to_the_default_queue(ui, fake_orchestrator):
    # A mistyped link should show the normal queue, not an error page.
    response = ui.get("/review/?state=nonsense")

    assert response.status_code == 200
    queue_calls = fake_orchestrator.requests_to("GET", "/review/queue")
    assert [value for name, value in queue_calls[0].query if name == "state"] == [
        "awaiting_review",
        "failed",
    ]


def test_prova_filter_matches_like_folder_names(ui, fake_orchestrator):
    # Setup: the failed job has prova "Enduro". We filter by "enduro" in
    # lower case; provas are compared the way studio folders are named, so
    # case and accents do not matter.
    fake_orchestrator.add_job(make_job("job-other-prova", prova="Aceleração"))

    html = ui.get("/review/?prova=enduro").text

    assert "job-failed-1" in html
    # Jobs with another prova, or with no prova yet, are filtered out.
    assert "job-other-prova" not in html
    assert "job-awaiting-1" not in html


def test_prova_menu_lists_the_provas_of_the_listed_jobs(ui, fake_orchestrator):
    fake_orchestrator.add_job(make_job("job-other-prova", prova="Aceleração"))

    html = ui.get("/review/").text

    # Both provas appear as choices in the prova <select>.
    assert ">Enduro</option>" in html
    assert ">Aceleração</option>" in html


def test_flagged_filter_keeps_only_low_res_jobs(ui, fake_orchestrator):
    fake_orchestrator.add_job(make_job("job-lowres-1", low_res=True))

    html = ui.get("/review/?flagged=1").text

    assert "job-lowres-1" in html
    assert "job-awaiting-1" not in html


def test_refresh_fragment_has_only_the_rows_and_keeps_the_filter(ui, fake_orchestrator):
    # The page polls this URL; it must apply the same filter as the page.
    response = ui.get("/review/partials/queue?state=failed")

    assert response.status_code == 200
    # A fragment, not a full page: no <html> or navigation bar.
    assert "<html" not in response.text
    assert "job-failed-1" in response.text
    assert "job-awaiting-1" not in response.text


def test_page_polls_with_the_current_filter(ui):
    html = ui.get("/review/?state=failed&prova=Enduro").text

    # The polling URL in the page repeats the filter, so refreshed rows
    # match what the reviewer chose.
    assert 'data-poll-url="/review/partials/queue?state=failed&amp;prova=Enduro"' in html


def test_empty_queue_says_so(ui, fake_orchestrator):
    fake_orchestrator.jobs.clear()

    html = ui.get("/review/").text

    assert "Nothing here" in html
