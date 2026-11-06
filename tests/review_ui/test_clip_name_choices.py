"""Settings → "Nome da equipe/escola nos vídeos" in the review UI (LIVESTACK-42 item 6).

The two choices are radio buttons on the Settings page (completo / curto
for the team, completo / sigla for the school). Besides the clip overlay,
they decide how the UI itself names a clip's team and school: on the job
page (the line under the car number) and on the clips page. The
orchestrator works the names out (`clip_team_name`, `clip_school_name`);
the UI shows them.
"""

from tests.review_ui.admin_browser import follow, post_form
from tests.review_ui.sample_data import make_manifest


def other_settings_fields() -> dict[str, str]:
    """The Settings form's other fields, at the harness's first-start values."""
    return {
        "intake_mode": "open",
        "inbox_hold_gb": "22",
        "proxy_evict_min_free_gb": "10",
        "discard_grace_hours": "24",
        "outbox_keep_unarchived_hours": "72",
        "installer_download_url": "",
        "event_id": "",
        "results_api_base_url": "https://api.bajasaebrasil.net",
    }


def test_the_settings_page_offers_both_choices_with_the_defaults_checked(admin):
    html = admin.get("/review/admin/settings").text

    assert "Nome da equipe nos vídeos" in html
    assert "Nome da escola nos vídeos" in html
    # Defaults: the team's short name and the school's sigla.
    assert 'name="clip_team_name_style" value="short" checked> curto' in html
    assert 'name="clip_team_name_style" value="full" > completo' in html
    assert 'name="clip_school_name_style" value="short" checked> sigla' in html


def test_saving_the_choices_reaches_the_orchestrator(admin, harness):
    fields = other_settings_fields()

    page = follow(
        admin,
        post_form(
            admin,
            "/review/admin/settings",
            **fields,
            clip_team_name_style="full",
            clip_school_name_style="short",
        ),
    )

    assert "Configurações salvas." in page.text
    stored = harness.context.admin_settings.current
    assert (stored.clip_team_name_style, stored.clip_school_name_style) == ("full", "short")
    # The page shows the new choice checked.
    assert 'name="clip_team_name_style" value="full" checked> completo' in page.text


def test_a_form_without_the_choices_keeps_them(admin, harness):
    # An older page (or a script) posting only the other fields changes nothing here.
    fields = other_settings_fields()
    post_form(admin, "/review/admin/settings", **fields, clip_team_name_style="full")
    post_form(admin, "/review/admin/settings", **fields)

    assert harness.context.admin_settings.current.clip_team_name_style == "full"


# --- Where the UI shows a clip's team (fake orchestrator) ---


def test_the_job_page_names_the_team_and_school_as_the_clip_will(ui, fake_orchestrator):
    # The orchestrator says this car's clip shows "FEI" from school "FEI".
    team = fake_orchestrator.teams[42]
    fake_orchestrator.teams[42] = team.model_copy(
        update={"clip_team_name": "FEI", "clip_school_name": "FEI"}
    )
    job = fake_orchestrator.jobs["job-awaiting-1"].model_copy(
        update={"car_number": 42, "team_name": "Equipe Cerrado"}
    )
    fake_orchestrator.add_job(job)

    html = ui.get("/review/jobs/job-awaiting-1").text

    assert "Equipe: <strong>FEI</strong> (FEI)" in html


def test_the_job_page_falls_back_to_the_jobs_team_name_when_the_lookup_fails(ui, fake_orchestrator):
    fake_orchestrator.teams_upstream_down = True
    job = fake_orchestrator.jobs["job-awaiting-1"].model_copy(
        update={"car_number": 42, "team_name": "Equipe Cerrado"}
    )
    fake_orchestrator.add_job(job)

    html = ui.get("/review/jobs/job-awaiting-1").text

    assert "Equipe: <strong>Equipe Cerrado</strong>" in html


def test_the_clips_page_shows_the_names_the_clip_shows(ui, fake_orchestrator):
    manifest = make_manifest()
    newest = manifest.clips[0].model_copy(
        update={"clip_team_name": "Cerrado", "clip_school_name": "UnB"}
    )
    fake_orchestrator.manifest = manifest.model_copy(
        update={"clips": [newest, *manifest.clips[1:]]}
    )

    html = ui.get("/review/clips").text

    assert 'Cerrado <span class="small">(UnB)</span>' in html
