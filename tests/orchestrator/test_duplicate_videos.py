"""Duplicate videos: the same file sent twice, or forwarded by someone else (LIVESTACK-51 item 3).

On the 2026-11-07 test, "duplicate detection is not working": only a
retry of the same WhatsApp *message* was recognised, so the same video in a
new message became a second job. The rules now (domain/duplicates.py):

* A video whose content (SHA-256) matches a job that is not discarded is a
  duplicate. For WhatsApp that SHA-256 is the message's `fileSha256` (the
  download checks the decrypted file against it); a dev test upload has no
  `fileSha256`, so the SHA-256 computed while storing it is used.
* The duplicate still becomes a job, discarded at once with "duplicate of
  <first job>", so it can be restored like any discard. Workers are never
  offered it.
* The bot answers it once: ⚠️ and the Settings text quoting the video
  ("já tinha sido recebido (às 11:00)"; empty text = no text).
* The first job lists its copies ("Também recebido de: Maria, 11:05").

`send_video` (orchestrator/testing/scenarios.py) encrypts the bytes like
WhatsApp (a fresh media key each time, so two sends of the same file are
different downloads with the same `fileSha256`, as on a real phone) and
posts the webhook. The harness clock starts at 2026-10-25 12:00 UTC
(09:00 in São Paulo) and only moves when a test moves it.
"""

import io

import pytest

from orchestrator.domain.dev_upload import ingest_test_upload
from orchestrator.testing.scenarios import (
    ask_next,
    hold_raw,
    job_id_for_message,
    make_proxy,
    send_video,
)
from shared.models import ReceivedAs, TaskKind, WorkerRole

MARIA = "5519981234567"  # Campinas (DDD 19)
ALESSANDER = "5511987650000"  # São Paulo (DDD 11)
VIDEO = b"the same 30 seconds of car 42 at the Enduro"


def send(harness, data=VIDEO, phone=MARIA, name="Maria (filmagem)", **kwargs):
    """Send `data` as a WhatsApp document; return (job id, the webhook payload)."""
    response, payload = send_video(harness, data, sender_phone=phone, sender_name=name, **kwargs)
    assert response.status_code == 202, response.text
    return job_id_for_message(harness, payload["data"]["key"]["id"]), payload


def review(harness, job_id, action):
    body = {"expected_version": harness.job(job_id)["version"], "action": action}
    return harness.client.patch(f"/jobs/{job_id}/review", json=body, auth=harness.reviewer_auth)


def test_the_same_video_forwarded_by_someone_else_is_a_discarded_duplicate(harness):
    # Maria sends the video; five minutes later Alessander forwards it.
    first, _ = send(harness)
    harness.clock.advance(300)
    copy, _ = send(harness, phone=ALESSANDER, name="Alessander")

    # The copy is a job of its own (so it can be restored), discarded at
    # once, pointing at the first one.
    copy_job = harness.job(copy)
    assert copy != first
    assert copy_job["state"] == "discarded"
    assert copy_job["duplicate_of"] == first
    assert copy_job["discarded_from"] == "received"
    assert copy_job["discard_grace_ends_at"] is not None
    # The first one is untouched and lists the copy, with who sent it and when.
    first_job = harness.job(first)
    assert first_job["state"] == "received"
    assert first_job["duplicate_of"] is None
    [listed] = first_job["duplicates"]
    assert listed["job_id"] == copy
    assert listed["sender_name"] == "Alessander"
    assert listed["received_at"] == "2026-10-25T12:05:00Z"
    # The event log says why.
    events = harness.client.get(f"/jobs/{copy}/events", auth=harness.reviewer_auth).json()
    reasons = [event["reason"] for event in events["events"]]
    assert f"duplicate of job {first}: the same video was already received" in reasons


def test_the_bot_answers_a_duplicate_once_with_the_settings_text(harness):
    send(harness)
    harness.clock.advance(300)
    copy, payload = send(harness, phone=ALESSANDER, name="Alessander")
    message_id = payload["data"]["key"]["id"]

    # Evolution retries the same webhook (a slow answer, a restart): the
    # message is recognised and nothing new is created or sent.
    retry = harness.client.post(
        "/intake/evolution-webhook", json=payload, headers=harness.webhook_headers
    )
    assert retry.json()["duplicate"] is True

    # One ⚠️ on the copy (never ✅), and one text quoting it that says when
    # the first copy came: 12:00 UTC is 09:00 in São Paulo.
    assert harness.evolution.reactions_on(message_id) == ["⚠️"]
    assert harness.evolution.texts_to(ALESSANDER) == [
        "⚠️ Este vídeo já tinha sido recebido (às 09:00). Não precisa reenviar."
    ]
    assert harness.job(copy)["state"] == "discarded"


def test_an_empty_text_on_settings_means_no_text(harness):
    # João empties "Vídeo repetido" on Configurações.
    response = harness.client.patch(
        "/admin/settings", json={"duplicate_video_message": "  "}, auth=harness.admin_auth
    )
    assert response.json()["settings"]["duplicate_video_message"] == ""
    send(harness)
    _, payload = send(harness, phone=ALESSANDER, name="Alessander")

    # Still marked ⚠️ (it was not used), but no text is sent.
    assert harness.evolution.reactions_on(payload["data"]["key"]["id"]) == ["⚠️"]
    assert harness.evolution.texts_to(ALESSANDER) == []


def test_the_first_copys_day_is_said_when_it_was_not_today(harness):
    send(harness)
    harness.clock.advance(26 * 3600)  # the next day, 11:00 in São Paulo
    send(harness, phone=ALESSANDER, name="Alessander")

    assert harness.evolution.texts_to(ALESSANDER) == [
        "⚠️ Este vídeo já tinha sido recebido (às 25/10 09:00). Não precisa reenviar."
    ]


def test_a_different_video_from_the_same_sender_is_not_a_duplicate(harness):
    first, _ = send(harness)
    other, _ = send(harness, data=b"car 42 again, second passagem")

    assert harness.job(other)["state"] == "received"
    assert harness.job(first)["duplicates"] == []


def test_a_test_upload_without_file_sha256_is_compared_by_its_own_sha256(harness):
    # The dev test upload has no WhatsApp message, so no fileSha256: the
    # orchestrator hashes the file while storing it. Two uploads of the
    # same bytes: the second is the duplicate.
    def upload():
        return ingest_test_upload(
            harness.context,
            io.BytesIO(VIDEO),
            "VID_0042.mp4",
            "video/mp4",
            "Alessander",
            ReceivedAs.TEST_UPLOAD,
        ).job_id

    first = upload()
    copy = upload()

    assert harness.job(first)["state"] == "received"
    assert harness.job(copy)["state"] == "discarded"
    assert harness.job(copy)["duplicate_of"] == first


def test_whatsapp_and_test_upload_of_the_same_file_match(harness):
    # Both paths store the same SHA-256 of the same bytes, so a file sent
    # on WhatsApp and then uploaded for a test is recognised too.
    first, _ = send(harness)
    copy = ingest_test_upload(
        harness.context, io.BytesIO(VIDEO), "x.mp4", "video/mp4", None, ReceivedAs.TEST_UPLOAD
    ).job_id

    assert harness.job(copy)["duplicate_of"] == first


def test_workers_are_never_offered_a_duplicate(harness):
    master = harness.enroll(WorkerRole.MASTER, "master", tailnet_address="100.70.0.9")
    first, _ = send(harness)
    copy, _ = send(harness, phone=ALESSANDER, name="Alessander")

    # The master is offered the first job's raw, and after that nothing:
    # the discarded copy is never downloaded by a worker.
    offer = ask_next(harness, master, [TaskKind.DOWNLOAD_RAW])
    assert offer["job_id"] == first
    hold_raw(harness, master, first)
    assert ask_next(harness, master, [TaskKind.DOWNLOAD_RAW]) is None
    assert harness.job(copy)["holders"] == []


def test_restoring_a_duplicate_makes_it_an_ordinary_video(harness):
    # The same file can show two cars passing: a reviewer restores the copy
    # to review it as a second video.
    master = harness.enroll(WorkerRole.MASTER, "master", tailnet_address="100.70.0.9")
    first, _ = send(harness)
    copy, _ = send(harness, phone=ALESSANDER, name="Alessander")

    restored = review(harness, copy, "restore")

    # Back where it was discarded from: received, so the workers get it.
    assert restored.status_code == 200, restored.text
    assert restored.json()["state"] == "received"
    hold_raw(harness, master, first)
    offer = ask_next(harness, master, [TaskKind.DOWNLOAD_RAW])
    assert offer["job_id"] == copy
    # It still says where it came from, and the first job still lists it.
    assert harness.job(copy)["duplicate_of"] == first
    assert [listed["job_id"] for listed in harness.job(first)["duplicates"]] == [copy]


def test_a_discarded_original_does_not_make_the_next_copy_a_duplicate(harness):
    # Maria's first copy was reviewed and discarded (a wrong file, say).
    master = harness.enroll(WorkerRole.MASTER, "master", tailnet_address="100.70.0.9")
    first, _ = send(harness)
    hold_raw(harness, master, first)
    make_proxy(harness, master, first)
    assert review(harness, first, "discard").json()["state"] == "discarded"

    # She sends it again: this time it is the video to use.
    again, payload = send(harness)

    # An ordinary new video: received, no link, and the usual ✅.
    assert harness.job(again)["state"] == "received"
    assert harness.job(again)["duplicate_of"] is None
    assert harness.evolution.reactions_on(payload["data"]["key"]["id"]) == ["✅"]


@pytest.mark.parametrize("copies", [2, 3])
def test_every_copy_points_at_the_first_job(harness, copies):
    # A video forwarded around the team: every copy names the first job,
    # never an earlier copy, and the first job lists them all in order.
    first, _ = send(harness)
    later = []
    for number in range(copies):
        harness.clock.advance(60)
        job_id, _ = send(harness, phone=f"55119876500{number:02d}", name=f"Pessoa {number}")
        later.append(job_id)

    assert [harness.job(job_id)["duplicate_of"] for job_id in later] == [first] * copies
    assert [listed["job_id"] for listed in harness.job(first)["duplicates"]] == later
