"""Small building blocks for tests: send a video, claim a task, upload, fetch like Caddy.

Every helper only uses the public HTTP API (plus reading files the way
Caddy would), so a test built from them exercises the real service.
"""

import hashlib
from urllib.parse import urlsplit

import httpx

from orchestrator.testing.harness import EnrolledWorker, OrchestratorHarness
from shared.models import TaskKind
from shared.protocol import (
    LEASE_ID_HEADER,
    UPLOAD_LENGTH_HEADER,
    UPLOAD_OFFSET_HEADER,
    UPLOAD_SHA256_HEADER,
)
from shared.testing.evolution_payloads import document_message_payload, video_message_payload
from shared.testing.whatsapp_media_encrypt import encrypt_bytes

# A few KB standing in for a video: the orchestrator never decodes raws.
SAMPLE_VIDEO_BYTES = bytes(range(256)) * 40
# Any bytes starting with the JPEG magic number pass the thumbnail check.
SAMPLE_JPEG = b"\xff\xd8\xff\xe0" + b"thumbnail" * 10
SAMPLE_PROXY = b"proxy-bytes-" * 500
SAMPLE_CLIP = b"final-clip-" * 700


def send_video(
    harness: OrchestratorHarness,
    data: bytes = SAMPLE_VIDEO_BYTES,
    *,
    as_video_message: bool = False,
    message_id: str | None = None,
    file_name: str = "VID_20261025_101500.mp4",
    sender_phone: str = "5511999999999",
) -> tuple[httpx.Response, dict]:
    """Encrypt `data` like WhatsApp, put it on the fake CDN and post the webhook.

    Returns (webhook response, webhook payload). The background download
    has already finished when this returns (the in-memory transport waits
    for background tasks).
    """
    media_type = "video" if as_video_message else "document"
    blob, encrypted = encrypt_bytes(data, media_type)
    path = f"/v/t62/{hashlib.sha256(blob).hexdigest()[:16]}.enc"
    url = harness.cdn.add_blob(path, blob)
    if as_video_message:
        payload = video_message_payload(
            encrypted, url, path, sender_phone=sender_phone, message_id=message_id
        )
    else:
        payload = document_message_payload(
            encrypted,
            url,
            path,
            file_name=file_name,
            sender_phone=sender_phone,
            message_id=message_id,
        )
    response = harness.client.post(
        "/intake/evolution-webhook", json=payload, headers=harness.webhook_headers
    )
    return response, payload


def job_id_for_message(harness: OrchestratorHarness, message_id: str) -> str | None:
    """The job created for a WhatsApp message id (None if none was created)."""
    with harness.context.database.transaction() as connection:
        row = connection.execute(
            "SELECT job_id FROM intake_messages WHERE message_id = ?", (message_id,)
        ).fetchone()
    return row["job_id"] if row else None


def receive_job(harness: OrchestratorHarness, data: bytes = SAMPLE_VIDEO_BYTES, **kwargs) -> str:
    """Send a video and return the id of the job it created."""
    response, payload = send_video(harness, data, **kwargs)
    assert response.status_code == 202, response.text
    job_id = job_id_for_message(harness, payload["data"]["key"]["id"])
    assert job_id is not None, "the background download did not create a job"
    return job_id


def ask_next(
    harness: OrchestratorHarness, worker: EnrolledWorker, kinds: list[TaskKind]
) -> dict | None:
    """POST /next; returns the offered task (a dict) or None."""
    response = harness.client.post(
        f"/workers/{worker.worker_id}/next",
        json={"accept_kinds": [str(kind) for kind in kinds]},
        headers=worker.headers,
    )
    assert response.status_code == 200, response.text
    return response.json()["task"]


def claim(
    harness: OrchestratorHarness, worker: EnrolledWorker, job_id: str, kind: TaskKind
) -> httpx.Response:
    return harness.client.post(
        f"/jobs/{job_id}/claim", json={"task_kind": str(kind)}, headers=worker.headers
    )


def claim_ok(
    harness: OrchestratorHarness, worker: EnrolledWorker, job_id: str, kind: TaskKind
) -> tuple[str, dict]:
    """Claim and insist it works; returns (lease id, task)."""
    response = claim(harness, worker, job_id, kind)
    assert response.status_code == 200, response.text
    body = response.json()
    return body["lease"]["lease_id"], body["task"]


def path_of(absolute_url: str) -> str:
    """The path part of a task URL, to send it through the in-memory client."""
    return urlsplit(absolute_url).path


def fetch_like_caddy(
    harness: OrchestratorHarness, worker: EnrolledWorker, absolute_url: str
) -> tuple[int, bytes | None]:
    """What Caddy does for /inbox/* and /outbox/*: ask /auth/file, then serve the file."""
    path = path_of(absolute_url)
    verdict = harness.client.get(
        "/auth/file",
        headers={**worker.headers, "X-Forwarded-Uri": path, "X-Forwarded-Method": "GET"},
    )
    if verdict.status_code != 204:
        return verdict.status_code, None
    return 200, (harness.context.settings.data_dir / path.lstrip("/")).read_bytes()


def upload_in_chunks(
    harness: OrchestratorHarness,
    worker: EnrolledWorker,
    url_path: str,
    lease_id: str,
    data: bytes,
    chunk_size: int = 1000,
) -> httpx.Response:
    """Send `data` as chunks; returns the last response (complete, or the first error)."""
    sha256 = hashlib.sha256(data).hexdigest()
    offset = 0
    while True:
        chunk = data[offset : offset + chunk_size]
        response = harness.client.post(
            url_path,
            content=chunk,
            headers={
                **worker.headers,
                UPLOAD_OFFSET_HEADER: str(offset),
                UPLOAD_LENGTH_HEADER: str(len(data)),
                UPLOAD_SHA256_HEADER: sha256,
                LEASE_ID_HEADER: lease_id,
            },
        )
        if response.status_code != 200 or response.json()["complete"]:
            return response
        offset = response.json()["received_bytes"]


def review_to_ready(
    harness: OrchestratorHarness,
    job_id: str,
    *,
    prova: str = "Enduro",
    car_number: int = 42,
    passagem: int = 1,
    cut_in_s: float = 1.0,
    cut_out_s: float = 5.0,
) -> dict:
    job = harness.job(job_id)
    response = harness.client.patch(
        f"/jobs/{job_id}/review",
        json={
            "expected_version": job["version"],
            "action": "ready",
            "prova": prova,
            "car_number": car_number,
            "passagem": passagem,
            "cut_in_s": cut_in_s,
            "cut_out_s": cut_out_s,
        },
        auth=harness.reviewer_auth,
    )
    assert response.status_code == 200, response.text
    return response.json()


def hold_raw(harness: OrchestratorHarness, worker: EnrolledWorker, job_id: str) -> None:
    """Claim download_raw and confirm it with the right hash: `worker` becomes a holder."""
    lease_id, task = claim_ok(harness, worker, job_id, TaskKind.DOWNLOAD_RAW)
    response = harness.client.post(
        f"/jobs/{job_id}/holder-confirm",
        json={
            "lease_id": lease_id,
            "sha256": task["sha256"],
            "size_bytes": task["size_bytes"],
            "duration_s": 12.0,
            "has_audio": True,
        },
        headers=worker.headers,
    )
    assert response.status_code == 200, response.text


def make_proxy(harness: OrchestratorHarness, worker: EnrolledWorker, job_id: str) -> None:
    """Claim make_proxy, upload a thumbnail and the proxy: the job reaches awaiting_review."""
    lease_id, _ = claim_ok(harness, worker, job_id, TaskKind.MAKE_PROXY)
    thumbnail = harness.client.put(
        f"/jobs/{job_id}/thumbnail",
        content=SAMPLE_JPEG,
        headers={**worker.headers, LEASE_ID_HEADER: lease_id},
    )
    assert thumbnail.status_code == 204, thumbnail.text
    proxy = upload_in_chunks(harness, worker, f"/jobs/{job_id}/proxy", lease_id, SAMPLE_PROXY)
    assert proxy.status_code == 200 and proxy.json()["complete"], proxy.text
