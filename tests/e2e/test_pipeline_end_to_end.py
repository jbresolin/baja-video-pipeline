"""The whole pipeline, end to end: WhatsApp → holders → proxy → review → render → studio.

This is the one test that runs every package together, as real code:
  * the orchestrator app (in-process, with the harness's fake WhatsApp CDN,
    Evolution, Tailscale and Baja app);
  * three real worker daemons as background threads: the master, one
    volunteer worker and the studio (stub ffmpeg, see tests/e2e/fleet.py);
  * the real review UI app, through which a "reviewer" marks the cut.

Nothing runs ffmpeg, opens a socket or touches the internet, so it also
runs in CI. Each step below waits (with a timeout) for the daemons to do
their part on their own, then checks what the design says should be true.
"""

import pytest

from orchestrator.testing.scenarios import SAMPLE_VIDEO_BYTES, receive_job, review_to_ready
from shared.models import WorkerRole
from tests.e2e.fleet import build_system, wait_until


@pytest.fixture
def system(tmp_path):
    # Build everything, and always stop the daemon threads afterwards, even
    # when an assertion fails half-way (otherwise threads would keep running).
    built = build_system(tmp_path)
    yield built
    built.stop()


def job_of(system, job_id: str) -> dict:
    return system.harness.job(job_id)


def submit(browser, path: str, **fields) -> None:
    """Post an admin form like a browser; success is a 303 back to the page."""
    response = browser.post(
        path, data=fields, headers={"Content-Type": "application/x-www-form-urlencoded"}
    )
    assert response.status_code == 303, response.text


def admin_api(system, path: str) -> dict:
    """Read the orchestrator directly with the admin token (to look up ids)."""
    return system.harness.client.get(path, headers=system.harness.admin_headers).json()


def test_a_whatsapp_video_reaches_the_studio_through_the_real_services(system):
    # --- Setup: the studio first, so it is online (heartbeat within 45 s)
    # when the render is offered and the orchestrator chooses a direct push.
    studio = system.start_worker(WorkerRole.STUDIO, "studio.test")
    master = system.start_worker(WorkerRole.MASTER, "master.test")
    volunteer = system.start_worker(WorkerRole.WORKER, "volunteer.test")
    master_id, volunteer_id = master.config.worker_id, volunteer.config.worker_id

    # --- 0. The admin prepares the event on the admin pages (plain HTML
    # forms, like a browser): a prova, the team of car 42, an allowed
    # contact whose videos default to that prova, and "allowlist" intake.
    admin = system.admin_ui
    submit(admin, "/review/admin/provas/create", name="Enduro")
    submit(admin, "/review/admin/teams/save", car_number="42", team_name="Equipe Mangue Baja")
    enduro_id = next(p["id"] for p in admin_api(system, "/provas")["provas"])
    submit(
        admin,
        "/review/admin/contacts/create",
        phone="+55 81 99876-5432",
        display_name="Maria (filmagem)",
        status="allowed",
        default_prova_id=str(enduro_id),
    )
    submit(
        admin,
        "/review/admin/settings",
        intake_mode="allowlist",
        inbox_hold_gb="22",
        proxy_evict_min_free_gb="10",
        installer_download_url="",
    )

    # --- 1. Intake: the fake CDN serves the encrypted file and a fake
    # Evolution webhook announces it, from that contact. The orchestrator
    # decrypts it into the inbox and creates the job, with the contact's
    # default prova already filled in.
    job_id = receive_job(system.harness, SAMPLE_VIDEO_BYTES, sender_phone="5581998765432")
    inbox_file = system.harness.context.layout.inbox_path(job_id, ".mp4")
    assert job_of(system, job_id)["prova"] == "Enduro"

    # --- 2. Holders and proxy happen on their own: both workers download
    # the raw through "Caddy", the first holder renders and uploads the
    # proxy, and the job lands in the review queue.
    wait_until(lambda: job_of(system, job_id)["state"] == "awaiting_review", what="review queue")
    wait_until(
        lambda: (
            len([h for h in job_of(system, job_id)["holders"] if h["status"] == "confirmed"]) == 2
        ),
        what="two confirmed holders",
    )
    job = job_of(system, job_id)
    holders = {holder["worker_id"] for holder in job["holders"]}
    # Design §4: the master is always one of the two holders.
    assert holders == {master_id, volunteer_id}
    # Two holders confirmed, so the VPS deleted its inbox copy (transit only).
    assert job["raw_on_vps"] is False
    assert not inbox_file.exists()
    # The raws really came through Caddy's /inbox/*, allowed by /auth/file.
    assert f"/inbox/{job_id}.mp4" in system.caddy.served_paths
    # The worker reported the proxy's frame rate (the stub probe says 29.97).
    assert job["frame_rate"] == pytest.approx(29.97)
    assert job["proxy_available"] is True

    # --- 3. Review through the real review UI: the page renders, then the
    # page script's JSON call marks the cut and the fields and says "ready".
    page = system.review_ui.get(f"/review/jobs/{job_id}")
    assert page.status_code == 200
    reviewed = system.review_ui.patch(
        f"/review/api/jobs/{job_id}/review",
        json={
            "expected_version": job["version"],
            "action": "ready",
            "prova": "Enduro",
            "car_number": 42,
            "passagem": 3,
            "cut_in_s": 1.0,
            "cut_out_s": 9.0,
            "show_pass": True,
        },
    )
    assert reviewed.status_code == 200, reviewed.text
    assert reviewed.json()["job"]["team_name"] == "Equipe Mangue Baja"

    # --- 4. A holder claims the render, renders it (stub ffmpeg) and pushes
    # it straight to the studio, which stores it, rewrites latest.m3u and
    # ACKs; the orchestrator then moves the job to done.
    wait_until(lambda: job_of(system, job_id)["state"] == "done", what="job done")
    job = job_of(system, job_id)
    assert job["clip"]["route"] == "direct"
    assert job["clip"]["relative_path"] == "enduro/42/enduro-042-p3.mp4"
    assert job["clip"]["rendered_by_worker_id"] in {master_id, volunteer_id}
    # Nothing went through the outbox.
    assert not any(path.startswith("/outbox/") for path in system.caddy.served_paths)

    # --- 5. On the studio box: the clip at <prova>/<car>/<passagem>.mp4 and
    # a latest.m3u that lists it, as OBS expects (design §7).
    box = studio.library.root
    clip_file = box / "enduro" / "42" / "enduro-042-p3.mp4"
    assert clip_file.is_file()
    playlist = (box / "latest.m3u").read_text(encoding="utf-8")
    assert playlist.startswith("#EXTM3U")
    assert "enduro/42/enduro-042-p3.mp4" in playlist
    assert "Equipe Mangue Baja" in playlist

    # --- 6. The manifest lists the delivered clip with its hash.
    manifest = system.harness.client.get("/manifest", auth=system.harness.reviewer_auth).json()
    assert [entry["job_id"] for entry in manifest["clips"]] == [job_id]
    assert manifest["clips"][0]["relative_path"] == "enduro/42/enduro-042-p3.mp4"
    assert manifest["clips"][0]["sha256"] == job["clip"]["sha256"]

    # --- 7. The event log tells the whole story, in order.
    events = system.harness.client.get(
        f"/jobs/{job_id}/events", auth=system.harness.reviewer_auth
    ).json()["events"]
    states = [event["to_state"] for event in events if event["kind"] == "state_changed"]
    assert states == [
        "proxy_ready",
        "awaiting_review",
        "ready",
        "claimed",
        "uploaded",
        "delivered",
        "done",
    ]
    assert {"holder_confirmed", "lease_acquired"} <= {event["kind"] for event in events}
    # The review UI's job page shows that same log.
    assert "aguardando revisão" in system.review_ui.get(f"/review/jobs/{job_id}").text


def test_a_clip_left_in_the_outbox_is_pulled_and_confirmed_by_the_studio(system):
    # This is the delivery route used when the studio is offline at render
    # time, or when the rendering PC cannot reach it (a worker on a shared
    # tailnet). On 2026-11-06 the studio pulled the clip but its ACK
    # (POST /outbox/<job_id>/ack) was stopped by Caddy's file check with
    # 403, so the job never left "uploaded" and the studio retried forever.

    # Arrange: only the master runs, so there is no studio to push to. A
    # video arrives, gets its proxy, and the reviewer marks it ready.
    master = system.start_worker(WorkerRole.MASTER, "master.test")
    job_id = receive_job(system.harness, SAMPLE_VIDEO_BYTES)
    wait_until(lambda: job_of(system, job_id)["state"] == "awaiting_review", what="review queue")
    review_to_ready(system.harness, job_id, prova="Enduro", car_number=42, passagem=3)
    # The master renders it and, with no studio online, leaves it in the outbox.
    wait_until(lambda: job_of(system, job_id)["state"] == "uploaded", what="clip in the outbox")
    assert job_of(system, job_id)["clip"]["route"] == "outbox", (
        "with no studio online the clip must go to the outbox"
    )

    # Act: the streaming PC comes online.
    studio = system.start_worker(WorkerRole.STUDIO, "studio.test")

    # Assert: the studio downloads the clip through Caddy's /outbox/ and
    # its ACK reaches the orchestrator, so the job is delivered and closed.
    wait_until(lambda: job_of(system, job_id)["state"] == "done", what="job done")
    assert f"/outbox/{job_id}.mp4" in system.caddy.served_paths, (
        "the studio must fetch the clip from the outbox through Caddy"
    )
    clip_file = studio.library.root / "enduro" / "42" / "enduro-042-p3.mp4"
    assert clip_file.is_file(), "the clip must be in the studio's clip folder"
    assert job_of(system, job_id)["clip"]["delivered_at"] is not None, (
        "the studio's ACK must have been accepted (it was refused with 403 before)"
    )
    assert master.config.worker_id == job_of(system, job_id)["clip"]["rendered_by_worker_id"]


def test_a_reviewer_watches_and_downloads_the_clip_on_air(system):
    # LIVESTACK-52: after a clip went straight to the studio, the reviewer
    # watches its preview and downloads it, with nothing but the real
    # services talking to each other.

    # Arrange: studio first (so the render is pushed straight to it), then
    # the master and a volunteer. A video is reviewed and rendered.
    studio = system.start_worker(WorkerRole.STUDIO, "studio.test")
    system.start_worker(WorkerRole.MASTER, "master.test")
    system.start_worker(WorkerRole.WORKER, "volunteer.test")
    job_id = receive_job(system.harness, SAMPLE_VIDEO_BYTES)
    wait_until(lambda: job_of(system, job_id)["state"] == "awaiting_review", what="review queue")
    review_to_ready(system.harness, job_id, prova="Enduro", car_number=42, passagem=3)
    wait_until(lambda: job_of(system, job_id)["state"] == "done", what="job done")
    clip_on_air = (studio.library.root / "enduro/42/enduro-042-p3.mp4").read_bytes()

    # "Ver clipe": the worker that rendered it sent its preview after the
    # delivery, and the job page plays it.
    wait_until(
        lambda: job_of(system, job_id)["clip_preview"]["state"] == "ready", what="clip preview"
    )
    preview = job_of(system, job_id)["clip_preview"]
    page = system.review_ui.get(f"/review/jobs/{job_id}").text
    assert f'src="{preview["url"]}"' in page

    # "Baixar clipe" through the review UI: the clip is not in the outbox,
    # so a PC that has it is asked to upload it; the page polls until ready.
    asked = system.review_ui.post(f"/review/api/jobs/{job_id}/clip-download", json={})
    assert asked.status_code == 200, asked.text
    url = f"/review/api/jobs/{job_id}/clip-download"
    ready = wait_until(
        lambda: (answer := system.review_ui.get(url).json())["state"] == "ready" and answer,
        what="clip uploaded for download",
    )
    assert ready["file_name"] == "enduro-042-p3.mp4"

    # The browser follows the link (Caddy passes it to the orchestrator)
    # with its session cookie, and gets exactly the clip that is on air.
    cookie = f"bvp_session={system.review_ui.cookies['bvp_session']}"
    download = system.harness.client.get(ready["download_url"], headers={"Cookie": cookie})
    assert download.status_code == 200
    assert download.content == clip_on_air
    downloaded = system.review_ui.get(url).json()
    assert downloaded["downloaded_at"] is not None
