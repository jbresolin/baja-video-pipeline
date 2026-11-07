"""The passagem panel against the REAL orchestrator (LIVESTACK-45).

The other panel tests use the fake orchestrator. This one runs the review
UI on top of the real one (orchestrator.testing.harness), so the whole
path is exercised: intake and probe report give each video its recording
time, the orchestrator lists the car's videos by prova folder, and the UI
turns that into the panel. The prova is renamed and a sibling discarded
on the way, as happens during an event.
"""

from datetime import UTC, datetime

import pytest

from orchestrator.testing.harness import REVIEWER_NAME, REVIEWER_PASSWORD, build_harness
from orchestrator.testing.scenarios import (
    ensure_prova,
    probe_and_make_proxy,
    receive_job,
    save_review_fields,
)
from shared.models import WorkerRole
from tests.review_ui.admin_browser import signed_in_browser


@pytest.fixture
def harness(tmp_path):
    """The real orchestrator on 2026-03-26 at 14:55 on the track, with car 7 and Suspensão."""
    built = build_harness(tmp_path / "data", clock_start=datetime(2026, 3, 26, 17, 55, tzinfo=UTC))
    built.add_team(7, "Baja UFPR")
    ensure_prova(built, "Suspensão")
    yield built
    built.client.close()
    built.context.database.close()


def video_filmed_at(harness, master, creation_time: str) -> str:
    job_id = receive_job(harness)
    report = {
        "format": {"tags": {"creation_time": creation_time}},
        "streams": [{"codec_type": "video"}],
        "location_present": False,
    }
    probe_and_make_proxy(harness, master, job_id, report)
    return job_id


def test_the_panel_follows_a_renamed_prova_and_ignores_a_discarded_video(harness):
    master = harness.enroll(WorkerRole.MASTER, "master", tailnet_address="100.70.0.9")
    # Three videos of car 7, filmed 14:02, 14:31 and 14:50 on the track.
    pass_1 = video_filmed_at(harness, master, "2026-03-26T17:02:00Z")
    pass_2 = video_filmed_at(harness, master, "2026-03-26T17:31:00Z")
    this_video = video_filmed_at(harness, master, "2026-03-26T17:50:00Z")
    save_review_fields(harness, pass_1, prova="Suspensão", car_number=7, passagem=1)
    job = save_review_fields(harness, pass_2, prova="Suspensão", car_number=7, passagem=2)
    save_review_fields(harness, this_video, prova="Suspensão", car_number=7)

    browser = signed_in_browser(harness, REVIEWER_NAME, REVIEWER_PASSWORD)

    # The job page: both passes listed, 3 suggested.
    page = browser.get(f"/review/jobs/{this_video}").text
    assert "Outros vídeos de Suspensão, carro 7" in page
    assert "Passagens já registradas: 1 (14:02), 2 (14:31)" in page
    assert "Sugestão: passagem 3 — gravado depois da passagem 2, de 14:31" in page

    # The admin renames the prova (its folder, "suspensao", stays).
    prova = ensure_prova(harness, "Suspensão")
    harness.client.patch(
        f"/admin/provas/{prova['id']}",
        json={"name": "Suspensão e Tração"},
        headers=harness.admin_headers,
    ).raise_for_status()
    # The page the reviewer has open still says "Suspensão": the panel
    # still finds the car's videos, by folder.
    answer = browser.get(
        f"/review/api/jobs/{this_video}/passagem-help",
        params={"prova": "Suspensão", "car_number": 7, "passagem": 2},
    ).json()
    assert answer["warnings"] == ["A passagem 2 já é o vídeo de 14:31."]

    # The 14:31 video is discarded (an accidental recording): passagem 2 is free.
    harness.client.patch(
        f"/jobs/{pass_2}/review",
        json={"expected_version": job["version"], "action": "discard"},
        auth=harness.reviewer_auth,
    ).raise_for_status()
    answer = browser.get(
        f"/review/api/jobs/{this_video}/passagem-help",
        params={"prova": "Suspensão e Tração", "car_number": 7, "passagem": 2},
    ).json()
    assert answer["warnings"] == []
    assert answer["suggestion"] == 2
    assert f"/review/jobs/{pass_2}" not in answer["html"]
