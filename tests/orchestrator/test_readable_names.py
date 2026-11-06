"""Readable file names, orchestrator side (LIVESTACK-41 item 12).

  * Raws: the job gets its readable worker-side name when the bot receives
    the video ("<sender>-<YYYYMMDD-HHMMSS>-<job id first 8>.<ext>"), sender
    = the contact's display name (admin-editable), time in São Paulo. The
    download_raw offer carries it; the VPS inbox keeps "<job_id>.<ext>".
    Renaming the contact later renames nothing.
  * Clips: the process offer's clip path is "<prova>/<car>/<prova>-<car>-p<n>.mp4",
    and the job says what a downloaded clip should be called.

The harness clock reads 2026-10-25 12:00 UTC = 09:00 in São Paulo.
"""

from orchestrator.testing.scenarios import (
    ask_next,
    claim_ok,
    hold_raw,
    make_proxy,
    receive_job,
    review_to_ready,
)
from shared.models import TaskKind, WorkerRole

PHONE = "5511999999999"


def test_the_raw_is_named_after_the_sender_and_the_time_the_bot_got_it(harness):
    master = harness.enroll(WorkerRole.MASTER, "master")
    job_id = receive_job(harness)  # sent by "Voluntário Teste"

    expected = f"voluntario-teste-20261025-090000-{job_id[:8]}.mp4"
    assert harness.job(job_id)["source"]["raw_file_name"] == expected
    offer = ask_next(harness, master, [TaskKind.DOWNLOAD_RAW])
    assert offer["raw_file_name"] == expected
    # The VPS inbox keeps the job id: /auth/file and the cleanup match on it.
    assert offer["download_url"].endswith(f"/inbox/{job_id}.mp4")
    assert harness.context.layout.inbox_path(job_id, ".mp4").exists()


def test_the_admins_name_for_the_contact_wins_and_a_rename_renames_nothing(harness):
    first = receive_job(harness)
    renamed = harness.client.patch(
        f"/admin/contacts/{PHONE}",
        json={"display_name": "João Guilherme Bresolin"},
        headers=harness.admin_headers,
    )
    assert renamed.status_code == 200, renamed.text
    harness.clock.advance(60)

    second = receive_job(harness)

    assert harness.job(first)["source"]["raw_file_name"].startswith("voluntario-teste-")
    assert harness.job(second)["source"]["raw_file_name"] == (
        f"joao-guilherme-bresolin-20261025-090100-{second[:8]}.mp4"
    )


def test_the_clip_path_and_download_name_say_prova_car_and_passagem(harness):
    master = harness.enroll(WorkerRole.MASTER, "master")
    job_id = receive_job(harness)
    hold_raw(harness, master, job_id)
    make_proxy(harness, master, job_id)
    review_to_ready(harness, job_id, prova="Enduro", car_number=7, passagem=12)

    _, task = claim_ok(harness, master, job_id, TaskKind.PROCESS)

    assert task["clip_relative_path"] == "enduro/7/enduro-007-p12.mp4"
