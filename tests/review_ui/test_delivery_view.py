"""Tests for "where did the clip go?" (LIVESTACK-38 item 5).

After the first real video nobody could tell from the UI that the clip
had gone to the VPS outbox (no studio online) and that the worker had
deleted its own copy. Now the job page's status strip and the clips page
say where the finished clip is, and offer a Download while the outbox has it.
"""

from datetime import timedelta

from review_ui.delivery_view import delivery_notes
from shared.models import ClipInfo, ClipVersion, DeliveryRoute, JobState
from tests.review_ui.sample_data import MASTER_ID, T0, delivered_job, make_job

SHA = "c" * 64


def uploaded_job(job_id="job-outbox-1", **clip_changes):
    """A job whose clip sits in the VPS outbox because no studio was online."""
    clip = ClipInfo(
        sha256=SHA,
        size_bytes=50_000_000,
        relative_path="enduro/42/3.mp4",
        route=DeliveryRoute.OUTBOX,
        rendered_by_worker_id=MASTER_ID,
        in_outbox=True,
        download_url=f"/clip-downloads/{job_id}.mp4",
        **clip_changes,
    )
    version = ClipVersion(
        version=1,
        sha256=SHA,
        size_bytes=50_000_000,
        relative_path="enduro/42/3.mp4",
        archive_relative_path="enduro/42/3.mp4",
        rendered_at=T0,
    )
    return make_job(
        job_id,
        JobState.UPLOADED,
        prova="Enduro",
        car_number=42,
        passagem=3,
        clip=clip,
        clip_versions=[version],
    )


def test_a_clip_in_the_outbox_says_so_and_is_not_archived_yet():
    texts = [note.text for note in delivery_notes(uploaded_job())]
    assert texts == [
        "Clipe na caixa de saída do servidor (estúdio offline).",
        "Ainda não arquivado no mestre (o worker que renderizou guarda uma cópia).",
    ]


def test_a_delivered_clip_says_when_and_how_it_reached_the_studio():
    notes = delivery_notes(delivered_job())
    first = notes[0]
    # "Entregue ao estúdio (enviado direto pelo worker, suspensao/7/1.mp4) às" + time.
    assert first.level == "success"
    assert first.text.startswith("Entregue ao estúdio (enviado direto pelo worker")
    assert first.at == T0 + timedelta(minutes=20)


def test_an_archived_clip_names_its_place_in_the_archive_and_older_versions():
    job = uploaded_job()
    archived_v2 = job.clip_versions[0].model_copy(
        update={"version": 2, "archive_relative_path": "enduro/42/3.v2.mp4", "archived_at": T0}
    )
    v1 = job.clip_versions[0].model_copy(update={"sha256": "d" * 64, "archived_at": None})
    job = job.model_copy(update={"clip_versions": [v1, archived_v2]})

    texts = [note.text for note in delivery_notes(job)]

    assert "Arquivado no mestre (enduro/42/3.v2.mp4) às" in texts
    assert "Versões anteriores no arquivo do mestre: 0 de 1." in texts


def test_the_job_page_offers_the_outbox_clip_for_download(ui, fake_orchestrator):
    fake_orchestrator.add_job(uploaded_job())
    html = ui.get("/review/jobs/job-outbox-1").text
    assert 'href="/clip-downloads/job-outbox-1.mp4" download' in html
    assert "Clipe na caixa de saída do servidor (estúdio offline)." in html


def test_no_download_when_the_outbox_no_longer_has_the_clip(ui):
    # The sample delivered job's clip went straight to the studio.
    html = ui.get("/review/jobs/job-done-1").text
    assert "/clip-downloads/" not in html


def test_the_clips_page_lists_outbox_clips_with_a_download_button(ui, fake_orchestrator):
    fake_orchestrator.add_job(uploaded_job())
    html = ui.get("/review/clips").text
    assert "Na caixa de saída do servidor (estúdio offline)" in html
    assert 'href="/clip-downloads/job-outbox-1.mp4" download>Baixar</a>' in html
