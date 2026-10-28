"""Tests for intake: POST /intake/evolution-webhook and POST /intake/test-upload.

The webhook must answer fast, create the job only after the media is
downloaded, decrypted and verified, ignore what is not a video, spot
duplicates, flag low_res videos, and protect the VPS inbox (design §10).
"""

import hashlib

from orchestrator.domain import bot_messages
from orchestrator.testing.harness import build_harness
from orchestrator.testing.scenarios import (
    SAMPLE_VIDEO_BYTES,
    job_id_for_message,
    receive_job,
    send_video,
)
from shared.testing.evolution_payloads import document_message_payload, text_message_payload
from shared.testing.whatsapp_media_encrypt import encrypt_bytes


def test_wrong_or_missing_webhook_secret_is_refused(harness):
    payload = text_message_payload()
    missing = harness.client.post("/intake/evolution-webhook", json=payload)
    wrong = harness.client.post(
        "/intake/evolution-webhook", json=payload, headers={"X-Webhook-Secret": "nope"}
    )
    assert missing.status_code == 401
    assert wrong.status_code == 401
    # Every error uses the same JSON shape with a machine-readable code.
    assert wrong.json()["error"]["code"] == "unauthenticated"


def test_a_document_video_becomes_a_received_job_with_the_raw_in_the_inbox(harness):
    response, payload = send_video(harness, SAMPLE_VIDEO_BYTES, file_name="Passagem 3.MOV")
    # 202 Accepted: the download happened in the background.
    assert response.status_code == 202
    assert response.json()["accepted"] is True
    assert response.json()["job_id"] is None

    job_id = job_id_for_message(harness, payload["data"]["key"]["id"])
    job = harness.job(job_id)
    assert job["state"] == "received"
    assert job["low_res"] is False
    assert job["sha256"] == hashlib.sha256(SAMPLE_VIDEO_BYTES).hexdigest()
    assert job["source"]["sender_name"] == "Voluntário Teste"
    # The original extension is kept, lowercased (docs/api.md §8.2).
    inbox_file = harness.context.layout.inbox_path(job_id, ".mov")
    assert inbox_file.read_bytes() == SAMPLE_VIDEO_BYTES
    # Exactly one bot reply for a good document: the "received" confirmation,
    # sent only after the file was verified and the job created.
    assert harness.evolution.texts_to("5511999999999") == [bot_messages.RECEIVED_OK]


def test_a_repeated_webhook_is_a_duplicate_pointing_at_the_same_job(harness):
    response, payload = send_video(harness)
    job_id = job_id_for_message(harness, payload["data"]["key"]["id"])
    # Evolution re-sends exactly the same body (same data.key.id).
    again = harness.client.post(
        "/intake/evolution-webhook", json=payload, headers=harness.webhook_headers
    )
    assert again.status_code == 200
    assert again.json()["duplicate"] is True
    assert again.json()["job_id"] == job_id
    # Still exactly one job.
    queue = harness.client.get("/review/queue?state=received", auth=harness.reviewer_auth)
    assert len(queue.json()["items"]) == 1


def test_a_whatsapp_video_message_is_accepted_as_low_res_and_the_bot_asks_for_a_document(harness):
    job_id = receive_job(harness, as_video_message=True, sender_phone="5511888888888")
    assert harness.job(job_id)["low_res"] is True
    replies = harness.evolution.texts_to("5511888888888")
    assert len(replies) == 1
    assert "documento" in replies[0]


def test_messages_that_are_not_videos_are_ignored_with_200(harness):
    # 200 (not an error) so Evolution does not retry it.
    text = harness.client.post(
        "/intake/evolution-webhook", json=text_message_payload(), headers=harness.webhook_headers
    )
    assert text.status_code == 200
    assert text.json()["ignored"] is True
    # A PDF sent as a document is not a video either.
    pdf = document_message_payload(mimetype="application/pdf", file_name="regulamento.pdf")
    response = harness.client.post(
        "/intake/evolution-webhook", json=pdf, headers=harness.webhook_headers
    )
    assert response.json()["ignored"] is True
    # Other Evolution events (connection updates...) are ignored without validation.
    other = harness.client.post(
        "/intake/evolution-webhook",
        json={"event": "connection.update", "data": {"state": "open"}},
        headers=harness.webhook_headers,
    )
    assert other.status_code == 200 and other.json()["ignored"] is True


def test_messages_sent_by_the_bot_itself_are_ignored(harness):
    payload = document_message_payload()
    payload["data"]["key"]["fromMe"] = True
    response = harness.client.post(
        "/intake/evolution-webhook", json=payload, headers=harness.webhook_headers
    )
    assert response.json()["ignored"] is True


def test_a_tampered_download_creates_no_job_and_the_bot_asks_for_a_resend(harness):
    # Encrypt the video, then flip one byte of the blob on the "CDN": the
    # encrypted-hash check must catch it. (A failed check never retries.)
    blob, encrypted = encrypt_bytes(SAMPLE_VIDEO_BYTES)
    tampered = bytes([blob[0] ^ 0xFF]) + blob[1:]
    url = harness.cdn.add_blob("/v/tampered.enc", tampered)
    payload = document_message_payload(encrypted, url, "/v/tampered.enc")
    response = harness.client.post(
        "/intake/evolution-webhook", json=payload, headers=harness.webhook_headers
    )
    # The webhook already answered 202 before the download ran...
    assert response.status_code == 202
    # ...but no job exists, nothing is left in the inbox, and the bot replied.
    assert job_id_for_message(harness, payload["data"]["key"]["id"]) is None
    assert list(harness.context.layout.inbox_dir.iterdir()) == []
    assert len(harness.evolution.texts_to("5511999999999")) == 1


def test_above_the_hard_cap_intake_answers_413_and_replies_only_once(tmp_path):
    # A tiny hard cap (0.00001 GB = 10 kB) that one sample video exceeds.
    harness = build_harness(tmp_path, inbox_hold_gb=0.000001, inbox_cap_gb=0.00001)
    big_video = b"x" * 20_000
    response, payload = send_video(harness, big_video)
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "inbox_full"
    # Evolution retries a failed delivery many times; the sender hears from
    # the bot only once.
    harness.client.post("/intake/evolution-webhook", json=payload, headers=harness.webhook_headers)
    assert len(harness.evolution.texts_to("5511999999999")) == 1


def test_above_the_soft_cap_the_video_is_accepted_and_the_sender_asked_to_hold(tmp_path):
    # Soft limit 1 kB, hard limit 1 MB: the sample video is between the two.
    harness = build_harness(tmp_path, inbox_hold_gb=0.000001, inbox_cap_gb=0.001)
    job_id = receive_job(harness)
    assert harness.job(job_id)["state"] == "received"
    replies = harness.evolution.texts_to("5511999999999")
    assert len(replies) == 1 and "espere" in replies[0]


def test_test_upload_is_off_unless_dev_mode(harness):
    response = harness.client.post(
        "/intake/test-upload",
        files={"file": ("clip.mp4", b"data", "video/mp4")},
        headers=harness.admin_headers,
    )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "dev_mode_disabled"


def test_test_upload_in_dev_mode_creates_a_job_synchronously(tmp_path):
    harness = build_harness(tmp_path, dev_mode=True)
    response = harness.client.post(
        "/intake/test-upload",
        files={"file": ("clip.mp4", SAMPLE_VIDEO_BYTES, "video/mp4")},
        data={"sender_name": "dev", "received_as": "video"},
        headers=harness.admin_headers,
    )
    assert response.status_code == 200
    job = harness.job(response.json()["job_id"])
    assert job["state"] == "received"
    assert job["low_res"] is True  # received_as=video simulates a low_res intake
    assert job["sha256"] == hashlib.sha256(SAMPLE_VIDEO_BYTES).hexdigest()
    # Admin token required: a reviewer cannot use it.
    refused = harness.client.post(
        "/intake/test-upload",
        files={"file": ("clip.mp4", b"x", "video/mp4")},
        auth=harness.reviewer_auth,
    )
    assert refused.status_code == 401


def post_real_video(harness, payload_changes: dict) -> tuple[dict, dict]:
    """Post a real, downloadable document webhook after applying `payload_changes` to its key.

    Returns (response JSON, payload). The video is genuinely encrypted on the
    fake CDN, so if the orchestrator did NOT ignore it, a job would appear.
    """
    blob, encrypted = encrypt_bytes(SAMPLE_VIDEO_BYTES)
    path = f"/v/{hashlib.sha256(blob + repr(payload_changes).encode()).hexdigest()[:12]}.enc"
    url = harness.cdn.add_blob(path, blob)
    payload = document_message_payload(encrypted, url, path, sender_phone="5511955554444")
    payload["data"]["key"].update(payload_changes)
    response = harness.client.post(
        "/intake/evolution-webhook", json=payload, headers=harness.webhook_headers
    )
    assert response.status_code in (200, 202), response.text
    return response.json(), payload


def test_group_and_broadcast_videos_are_ignored_while_the_same_video_by_dm_is_accepted(harness):
    # The Baja Bot number may sit in WhatsApp groups. Design §3: videos come
    # by DM only. Here the same document arrives four ways; only the DM counts.
    group_jid = {
        "remoteJid": "120363025246125486@g.us",
        "participant": "5511955554444@s.whatsapp.net",
    }
    # A group message where only `participant` gives the group away.
    participant_only = {"participant": "5511955554444@s.whatsapp.net"}
    status_broadcast = {"remoteJid": "status@broadcast"}

    ignored_answers = [
        post_real_video(harness, changes)
        for changes in (group_jid, participant_only, status_broadcast)
    ]
    dm_answer, dm_payload = post_real_video(harness, {})

    # Each non-DM one is answered 200 "ignored" (so Evolution does not
    # retry), creates no job, and leaves nothing in the inbox.
    for answer, payload in ignored_answers:
        assert answer["ignored"] is True
        assert "group" in answer["reason"]
        assert job_id_for_message(harness, payload["data"]["key"]["id"]) is None
    # The DM is accepted and becomes a job, with its raw in the inbox.
    assert dm_answer["accepted"] is True
    dm_job = job_id_for_message(harness, dm_payload["data"]["key"]["id"])
    assert harness.job(dm_job)["state"] == "received"
    assert len(list(harness.context.layout.inbox_dir.iterdir())) == 1
    # The bot said nothing into the group: its only message is the DM confirmation.
    assert [(m.number, m.text) for m in harness.evolution.sent] == [
        ("5511955554444", bot_messages.RECEIVED_OK)
    ]
