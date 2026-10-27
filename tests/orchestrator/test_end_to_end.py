"""One pass video, all the way from WhatsApp to the studio, through the HTTP API only.

The cast:
  * the fake webhook poster (orchestrator/fakes/fake_evolution_webhook.py)
    builds the Evolution webhook for an encrypted video on a fake CDN;
  * a master worker and two volunteer workers, one fast and one slow,
    played by plain HTTP calls;
  * a reviewer, using HTTP Basic auth;
  * the studio, which pulls the clip from the outbox and ACKs it.

At the end we check the job went through every state in order, that the
master and the fast worker were the two holders, that the VPS inbox copy
was deleted after both confirmed, and that the manifest lists the clip.
"""

import hashlib
import importlib.util
from pathlib import Path

from orchestrator.repositories.events import list_events_for_job
from orchestrator.testing import scenarios
from orchestrator.testing.scenarios import (
    SAMPLE_CLIP,
    ask_next,
    claim_ok,
    fetch_like_caddy,
    job_id_for_message,
    path_of,
    upload_in_chunks,
)
from shared.models import TaskKind, WorkerRole
from shared.testing.whatsapp_media_encrypt import encrypt_file

FAKES_FOLDER = Path(__file__).resolve().parents[2] / "orchestrator" / "fakes"


def load_fake_webhook_poster():
    """The dev tool is a script, not a package, so load it from its file."""
    spec = importlib.util.spec_from_file_location(
        "fake_evolution_webhook", FAKES_FOLDER / "fake_evolution_webhook.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def report_download_speed(harness, worker, mbps: float) -> None:
    """Send a heartbeat with one download measurement, so the worker has a known speed."""
    seconds = 10.0
    byte_count = int(mbps * 1_000_000 / 8 * seconds)
    harness.heartbeat(
        worker, measurements=[{"kind": "download", "byte_count": byte_count, "seconds": seconds}]
    )


def test_a_pass_video_goes_from_whatsapp_to_the_studio(harness, tmp_path):
    # --- Setup: enroll the fleet. Each enrollment also sends a first heartbeat.
    master = harness.enroll(WorkerRole.MASTER, "joao-desktop")
    fast_worker = harness.enroll(WorkerRole.WORKER, "fast-pc")
    slow_worker = harness.enroll(WorkerRole.WORKER, "slow-pc")
    studio = harness.enroll(WorkerRole.STUDIO, "streaming-pc")
    report_download_speed(harness, fast_worker, mbps=400)
    report_download_speed(harness, slow_worker, mbps=20)
    harness.baja_app.teams[42] = "Equipe Mangue Baja"

    # --- 1. A volunteer sends the video as a WhatsApp document.
    # The fake poster's own payload builder makes the webhook body.
    poster = load_fake_webhook_poster()
    video = tmp_path / "pass.mp4"
    video.write_bytes(bytes(range(256)) * 100)
    blob_folder = tmp_path / "cdn"
    blob_folder.mkdir()
    encrypted = encrypt_file(video, blob_folder / "blob.enc")
    harness.cdn.add_blob("/blob.enc", (blob_folder / "blob.enc").read_bytes())
    payload = poster.build_payload(
        video, encrypted, "http://fake-cdn.test", "blob.enc", as_video=False, sender_name="Ana"
    )
    webhook = harness.client.post(
        "/intake/evolution-webhook", json=payload, headers=harness.webhook_headers
    )
    # 202 = accepted; the download ran in the background before we got here.
    assert webhook.status_code == 202
    job_id = job_id_for_message(harness, payload["data"]["key"]["id"])
    assert job_id is not None
    job = harness.job(job_id)
    assert job["state"] == "received"
    # The job's hash is the SHA-256 of the *decrypted* video.
    assert job["sha256"] == hashlib.sha256(video.read_bytes()).hexdigest()
    inbox_file = harness.context.layout.inbox_path(job_id, ".mp4")
    assert inbox_file.exists()

    # --- 2. Holders. The master is always offered the download.
    master_offer = ask_next(harness, master, [TaskKind.DOWNLOAD_RAW])
    assert master_offer["job_id"] == job_id
    # The second slot goes to the fastest idle volunteer: the slow PC gets
    # nothing, the fast PC gets the offer.
    assert ask_next(harness, slow_worker, [TaskKind.DOWNLOAD_RAW]) is None
    fast_offer = ask_next(harness, fast_worker, [TaskKind.DOWNLOAD_RAW])
    assert fast_offer["job_id"] == job_id

    for worker in (master, fast_worker):
        lease_id, task = claim_ok(harness, worker, job_id, TaskKind.DOWNLOAD_RAW)
        # The worker downloads from Caddy, which asks /auth/file first.
        status, raw = fetch_like_caddy(harness, worker, task["download_url"])
        assert status == 200
        assert hashlib.sha256(raw).hexdigest() == task["sha256"]
        confirmed = harness.client.post(
            f"/jobs/{job_id}/holder-confirm",
            json={
                "lease_id": lease_id,
                "sha256": task["sha256"],
                "size_bytes": len(raw),
                "duration_s": 20.0,
                "has_audio": True,
            },
            headers=worker.headers,
        )
        assert confirmed.status_code == 200
    # The second confirmation deleted the VPS copy: the inbox is transit only.
    assert confirmed.json() == {"confirmed_holder_count": 2, "raw_deleted_from_vps": True}
    assert not inbox_file.exists()
    holders = {holder["worker_id"] for holder in harness.job(job_id)["holders"]}
    assert holders == {master.worker_id, fast_worker.worker_id}
    # The slow worker never got the raw, so Caddy would refuse it.
    status, _ = fetch_like_caddy(harness, slow_worker, master_offer["download_url"])
    assert status == 403

    # --- 3. A holder renders the proxy and thumbnail.
    proxy_offer = ask_next(harness, master, [TaskKind.MAKE_PROXY])
    assert proxy_offer["kind"] == "make_proxy"
    scenarios.make_proxy(harness, master, job_id)
    job = harness.job(job_id)
    assert job["state"] == "awaiting_review"
    assert job["proxy_url"] == f"/proxies/{job_id}.mp4"

    # --- 4. A reviewer marks the cut and the fields.
    queue = harness.client.get("/review/queue", auth=harness.reviewer_auth).json()
    assert [item["id"] for item in queue["items"]] == [job_id]
    reviewed = scenarios.review_to_ready(
        harness, job_id, prova="Enduro", car_number=42, passagem=3, cut_in_s=2, cut_out_s=9
    )
    assert reviewed["state"] == "ready"
    assert reviewed["team_name"] == "Equipe Mangue Baja"

    # --- 5. A holder processes it. The slow PC is not a holder: no offer.
    assert ask_next(harness, slow_worker, [TaskKind.PROCESS]) is None
    process_offer = ask_next(harness, fast_worker, [TaskKind.PROCESS])
    assert process_offer["clip_relative_path"] == "enduro/42/3.mp4"
    # The studio sent no tailnet address in its heartbeat, so it cannot be
    # pushed to: the orchestrator tells the worker to use the outbox.
    assert process_offer["delivery"] == {"mode": "outbox", "studio_url": None}
    lease_id, _ = claim_ok(harness, fast_worker, job_id, TaskKind.PROCESS)
    assert harness.job(job_id)["state"] == "claimed"
    # Direct push failed (say), so the worker uploads to the VPS outbox.
    uploaded = upload_in_chunks(
        harness, fast_worker, path_of(process_offer["outbox_upload_url"]), lease_id, SAMPLE_CLIP
    )
    assert uploaded.json()["complete"] is True
    assert harness.job(job_id)["state"] == "uploaded"

    # --- 6. The studio pulls the clip from the outbox and ACKs it.
    deliver_offer = ask_next(harness, studio, [TaskKind.DELIVER])
    deliver_lease, _ = claim_ok(harness, studio, job_id, TaskKind.DELIVER)
    status, clip_bytes = fetch_like_caddy(harness, studio, deliver_offer["download_url"])
    assert status == 200 and clip_bytes == SAMPLE_CLIP
    ack = harness.client.post(
        f"/outbox/{job_id}/ack",
        json={
            "lease_id": deliver_lease,
            "sha256": hashlib.sha256(clip_bytes).hexdigest(),
            "relative_path": deliver_offer["clip_relative_path"],
        },
        headers=studio.headers,
    )
    assert ack.json() == {"job_id": job_id, "state": "done"}
    # Delete-after-ACK: the outbox copy is gone.
    assert not harness.context.layout.outbox_path(job_id).exists()

    # --- 7. The manifest lists the clip for the playout UI.
    manifest = harness.client.get("/manifest", headers=studio.headers).json()
    assert len(manifest["clips"]) == 1
    entry = manifest["clips"][0]
    assert entry["relative_path"] == "enduro/42/3.mp4"
    assert entry["team_name"] == "Equipe Mangue Baja"
    assert entry["sha256"] == hashlib.sha256(SAMPLE_CLIP).hexdigest()

    # --- 8. The events log recorded every state, in order.
    with harness.context.database.transaction() as connection:
        events = list_events_for_job(connection, job_id)
    states = [event.to_state for event in events if event.to_state]
    assert states == [
        "received",
        "proxy_ready",
        "awaiting_review",
        "ready",
        "claimed",
        "uploaded",
        "delivered",
        "done",
    ]
    # No bot reply was needed: the video arrived as a document.
    assert harness.evolution.sent == []
