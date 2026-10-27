"""Tests for the clips page (delivery manifest) and the fleet page.

Setup (sample_data.py): the manifest has two clips, "enduro/42/3.mp4"
(newer) and "suspensao/7/1.mp4" (older). The fleet has the master
"joao-desktop" (online, NVENC, rendering a clip) and the volunteer
"maria-notebook" (offline for 7 minutes, near its storage cap).
"""

from review_ui.settings import ReviewUiSettings
from tests.review_ui.conftest import build_ui
from tests.review_ui.sample_data import REVIEWER_AUTHORIZATION

# --- Clips ---


def test_clips_are_listed_newest_first_with_their_studio_paths(ui):
    html = ui.get("/review/clips").text

    # The newer clip comes before the older one, as in the manifest.
    assert html.index("enduro/42/3.mp4") < html.index("suspensao/7/1.mp4")
    # Each path has a copy button holding exactly that path.
    assert 'data-copy="enduro/42/3.mp4"' in html
    assert "Equipe Cerrado" in html
    assert "2 delivered clips" in html


def test_clips_can_be_filtered_by_prova_ignoring_accents(ui):
    # "suspensao" without the accent still matches "Suspensão".
    html = ui.get("/review/clips?prova=suspensao").text

    assert "suspensao/7/1.mp4" in html
    assert "enduro/42/3.mp4" not in html


def test_clips_page_says_when_nothing_matches(ui):
    html = ui.get("/review/clips?prova=Aceleracao").text

    assert "No delivered clips for Aceleracao yet." in html


def test_copy_path_uses_the_studio_clip_root_when_configured(fake_orchestrator):
    # Setup: the studio box keeps clips under a Windows folder.
    settings = ReviewUiSettings(studio_clip_root="D:\\Baja\\clips")
    ui = build_ui(fake_orchestrator, settings, {"Authorization": REVIEWER_AUTHORIZATION})

    html = ui.get("/review/clips").text

    # The copied path is the full Windows path, with backslashes.
    assert 'data-copy="D:\\Baja\\clips\\enduro\\42\\3.mp4"' in html


# --- Fleet ---


def test_fleet_lists_workers_master_first_with_role_and_status(ui):
    html = ui.get("/review/fleet").text

    # The master is listed first even though the API sent it second.
    assert html.index("joao-desktop") < html.index("maria-notebook")
    assert "role-master" in html and "role-worker" in html
    assert '1 <span class="of">/ 2</span>' in html  # 1 of 2 workers online
    # Online/offline plus how long ago the last heartbeat was (relative to
    # "now", which is the real clock here, so we only check the words).
    assert "online" in html and "offline" in html
    assert "heartbeat" in html


def test_fleet_shows_held_raws_storage_and_stats(ui):
    html = ui.get("/review/fleet").text

    # Master: 12 held raws (6 GB); 200 GB used of a 500 GB cap.
    assert ">12<" in html.replace(" ", "")
    assert "200.0 GB of 500.0 GB" in html
    # Rolling stats, and dashes for a worker not measured yet.
    assert "92.5 Mbps" in html
    assert "41.0 s/GB" in html
    assert "—" in html
    # The volunteer is near its cap, so it takes no new raws.
    assert "near cap, no new raws" in html
    # What the master is doing right now (its lease).
    assert "process · job-clai" in html


def test_fleet_shows_vps_disk_and_job_counts(ui):
    html = ui.get("/review/fleet").text

    assert "3.0 GB of 25.0 GB" in html  # inbox against the hard cap
    assert "senders asked to hold at 22.0 GB" in html
    assert "40.0 GB" in html  # free disk
    # Job counts in pipeline order, including states with zero jobs. Each
    # count is a badge with a class named after its state.
    awaiting = html.index('state-awaiting_review">')
    ready = html.index('state-ready">')
    done = html.index('state-done">')
    assert awaiting < ready < done
    assert 'state-claimed">claimed</span> <b>0</b>' in html


def test_fleet_refresh_fragment(ui):
    response = ui.get("/review/partials/fleet")

    assert response.status_code == 200
    assert "<html" not in response.text
    assert "joao-desktop" in response.text


def test_fleet_error_is_shown_as_a_page(ui, fake_orchestrator):
    fake_orchestrator.fleet_fails = True

    response = ui.get("/review/fleet")

    # The orchestrator's 503 is shown as an error page, not a crash.
    assert response.status_code == 503
    assert "fleet is down for this test" in response.text
