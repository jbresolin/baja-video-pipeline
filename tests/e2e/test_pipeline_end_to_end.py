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

from orchestrator.testing.scenarios import SAMPLE_VIDEO_BYTES, receive_job
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


def test_a_whatsapp_video_reaches_the_studio_through_the_real_services(system):
    # --- Setup: the studio first, so it is online (heartbeat within 45 s)
    # when the render is offered and the orchestrator chooses a direct push.
    studio = system.start_worker(WorkerRole.STUDIO, "studio.test")
    master = system.start_worker(WorkerRole.MASTER, "master.test")
    volunteer = system.start_worker(WorkerRole.WORKER, "volunteer.test")
    master_id, volunteer_id = master.config.worker_id, volunteer.config.worker_id
    system.harness.baja_app.teams[42] = "Equipe Mangue Baja"

    # --- 1. Intake: the fake CDN serves the encrypted file and a fake
    # Evolution webhook announces it. The orchestrator decrypts it into the
    # inbox and creates the job.
    job_id = receive_job(system.harness, SAMPLE_VIDEO_BYTES)
    inbox_file = system.harness.context.layout.inbox_path(job_id, ".mp4")

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
    assert job["clip"]["relative_path"] == "enduro/42/3.mp4"
    assert job["clip"]["rendered_by_worker_id"] in {master_id, volunteer_id}
    # Nothing went through the outbox.
    assert not any(path.startswith("/outbox/") for path in system.caddy.served_paths)

    # --- 5. On the studio box: the clip at <prova>/<car>/<passagem>.mp4 and
    # a latest.m3u that lists it, as OBS expects (design §7).
    box = studio.library.root
    clip_file = box / "enduro" / "42" / "3.mp4"
    assert clip_file.is_file()
    playlist = (box / "latest.m3u").read_text(encoding="utf-8")
    assert playlist.startswith("#EXTM3U")
    assert "enduro/42/3.mp4" in playlist
    assert "Equipe Mangue Baja" in playlist

    # --- 6. The manifest lists the delivered clip with its hash.
    manifest = system.harness.client.get("/manifest", auth=system.harness.reviewer_auth).json()
    assert [entry["job_id"] for entry in manifest["clips"]] == [job_id]
    assert manifest["clips"][0]["relative_path"] == "enduro/42/3.mp4"
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
    assert "awaiting review" in system.review_ui.get(f"/review/jobs/{job_id}").text
