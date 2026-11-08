""" "Ver clipe" and "Baixar clipe" in the review UI (LIVESTACK-52).

What the reviewer sees:
  * the job page has a closed "Ver clipe" block with a player for the
    clip's 480p preview, or a sentence saying why there is none and
    pointing at "Baixar clipe";
  * the job page and every row of the clips page have "Baixar clipe",
    which asks the orchestrator and turns into a link (js/clip_review.js);
  * the clips page plays any row's preview in one dialog;
  * Configurações has "Gerar prévia dos clipes" and the two times.

The orchestrator decides the states (tests/orchestrator/test_clip_previews.py
and test_clip_downloads.py); these tests check what the pages make of them.
Setup is in conftest.py: `ui` is a signed-in browser on the review UI,
wired to the fake orchestrator.
"""

import pytest

from review_ui.catalog import text
from review_ui.settings import ReviewUiSettings
from shared.models import ClipPreviewState
from tests.review_ui.conftest import build_ui
from tests.review_ui.sample_data import SIGNED_IN_COOKIES, make_job, previewed_job
from tests.review_ui.test_admin_pages import follow, post_form
from tests.review_ui.test_templates_render import job_context, render


def test_the_job_page_plays_the_clips_preview(fake_orchestrator, ui):
    fake_orchestrator.add_job(previewed_job("job-previewed-1"))

    html = ui.get("/review/jobs/job-previewed-1").text

    # A closed block: the video loads only when the reviewer plays it.
    assert '<details class="clip-preview" id="clip-preview">' in html
    preview_url = f"/previews/job-previewed-1.{'b' * 64}.mp4"
    assert f'<video controls preload="none" playsinline src="{preview_url}">' in html
    assert "Ver clipe" in html


def test_after_a_reprocess_the_block_names_the_version(fake_orchestrator, ui):
    fake_orchestrator.add_job(previewed_job("job-previewed-2", version=2))
    assert "Ver clipe (versão 2)" in ui.get("/review/jobs/job-previewed-2").text


@pytest.mark.parametrize(
    "state", [state for state in ClipPreviewState if state != ClipPreviewState.READY]
)
def test_without_a_preview_the_block_says_why_and_points_at_the_download(state):
    # Every reason the orchestrator can give has its own sentence.
    html = render("job.html", job_context(previewed_job(state=state)))

    assert text(f"clip_preview.state.{state}") in html
    assert "Para conferir o clipe, use “Baixar clipe”." in html
    assert "<video controls preload" not in html


def test_a_job_without_a_clip_has_neither_block_nor_download_button(ui):
    html = ui.get("/review/jobs/job-awaiting-1").text
    assert 'id="clip-preview"' not in html
    assert "data-clip-download=" not in html


def test_any_clip_can_be_downloaded_from_the_job_page(ui):
    # The delivered job: its clip is on the studio, not in the outbox. The
    # button asks the orchestrator, whatever PC has the clip.
    html = ui.get("/review/jobs/job-done-1").text
    assert 'data-clip-download="job-done-1"' in html
    assert "data-clip-download-status" in html
    assert ">Baixar clipe</button>" in html


def test_the_clips_page_has_ver_clipe_and_baixar_clipe_on_every_row(ui):
    html = ui.get("/review/clips").text

    # The newer clip has a preview: its button carries the address and a title.
    assert f'data-preview-url="/previews/job-done-2.{"c" * 64}.mp4"' in html
    assert 'data-preview-title="Enduro · 42 · 3"' in html
    # The older one's preview was evicted: "sem prévia", with the reason on hover.
    evicted = text("clip_preview.state.evicted")
    assert f'title="{evicted}">sem prévia</span>' in html
    # Both rows can be downloaded, and one dialog plays the previews.
    assert html.count("data-clip-download=") == 2
    assert '<dialog class="clip-preview-dialog" id="clip-preview-dialog"' in html


def test_asking_for_the_clip_goes_to_the_orchestrator_and_the_link_is_absolute(
    fake_orchestrator,
):
    # With a public base URL, the link must point at the public site, where
    # Caddy passes /requested-clips/* to the orchestrator.
    settings = ReviewUiSettings(
        orchestrator_url="http://orch", public_base_url="https://pipeline.example"
    )
    ui = build_ui(fake_orchestrator, settings, SIGNED_IN_COOKIES)

    asked = ui.post("/review/api/jobs/job-done-1/clip-download", json={})
    status = ui.get("/review/api/jobs/job-done-1/clip-download")

    assert asked.status_code == 200 and asked.json()["state"] == "preparing"
    assert status.json()["download_url"] == (
        "https://pipeline.example/requested-clips/job-done-1/suspensao-007-p1.mp4"
    )
    assert status.json()["file_name"] == "suspensao-007-p1.mp4"
    assert fake_orchestrator.requests_to("POST", "/jobs/job-done-1/clip-download")


def test_a_job_without_a_clip_is_explained(fake_orchestrator, ui):
    fake_orchestrator.add_job(make_job("job-no-clip"))
    response = ui.post("/review/api/jobs/job-no-clip/clip-download", json={})
    assert response.status_code == 409


def test_a_browser_without_a_session_cannot_ask(anonymous_ui):
    # The review UI has no session to pass on, and the orchestrator refuses.
    response = anonymous_ui.post("/review/api/jobs/job-done-1/clip-download", json={})
    assert response.status_code == 401


# --- Configurações (against the real orchestrator) ---


SETTINGS_FORM = dict(
    intake_mode="open",
    inbox_hold_gb="22",
    proxy_evict_min_free_gb="10",
    installer_download_url="",
)


def test_the_settings_page_has_the_preview_switch_and_both_times(admin, harness):
    page = admin.get("/review/admin/settings").text
    assert "Gerar prévia dos clipes" in page
    assert 'name="clip_previews_enabled" value="1" checked' in page
    assert 'name="clip_download_keep_minutes"' in page
    assert 'name="clip_download_unused_hours"' in page

    # The admin unticks the box (a browser then sends nothing for it, only
    # the hidden "_shown" field) and changes both times.
    follow(
        admin,
        post_form(
            admin,
            "/review/admin/settings",
            **SETTINGS_FORM,
            clip_previews_enabled_shown="1",
            clip_download_keep_minutes="45",
            clip_download_unused_hours="12",
        ),
    )

    stored = harness.context.admin_settings.current
    assert stored.clip_previews_enabled is False
    assert (stored.clip_download_keep_minutes, stored.clip_download_unused_hours) == (45, 12)


def test_a_form_without_the_box_keeps_the_switch(admin, harness):
    # An older page (no "_shown" field) must not turn previews off by accident.
    follow(admin, post_form(admin, "/review/admin/settings", **SETTINGS_FORM))

    assert harness.context.admin_settings.current.clip_previews_enabled is True
