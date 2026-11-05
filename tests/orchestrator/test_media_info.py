"""Tests for PUT /jobs/{id}/media-info: the raw's ffprobe report on the job.

The first holder probes the raw anyway to make the proxy; it sends the
whole report so the review page can show container, codecs, HDR and the
recording phone under the player (docs/api.md §7.1).
"""

from orchestrator.testing.scenarios import claim_ok, hold_raw, receive_job
from shared.models import TaskKind, WorkerRole
from shared.protocol import LEASE_ID_HEADER

# What ffprobe says about an Android phone video, trimmed. The location tag
# must never reach the database.
ANDROID_REPORT = {
    "format": {
        "format_name": "mov,mp4,m4a,3gp,3g2,mj2",
        "duration": "12.000000",
        "tags": {
            "com.android.manufacturer": "samsung",
            "com.android.model": "SM-S918B",
            "location": "-23.5505-046.6333/",
        },
    },
    "streams": [
        {"codec_type": "video", "codec_name": "h264", "avg_frame_rate": "30/1"},
        {"codec_type": "audio", "codec_name": "aac", "channels": 2},
    ],
    "location_present": False,
}


def put_media_info(harness, worker, job_id, lease_id, body):
    return harness.client.put(
        f"/jobs/{job_id}/media-info",
        json=body,
        headers={**worker.headers, LEASE_ID_HEADER: lease_id},
    )


def holder_with_proxy_lease(harness):
    """A master holding a fresh job's raw, with the make_proxy lease claimed."""
    master = harness.enroll(WorkerRole.MASTER, "master", tailnet_address="100.70.0.9")
    job_id = receive_job(harness)
    hold_raw(harness, master, job_id)
    lease_id, task = claim_ok(harness, master, job_id, TaskKind.MAKE_PROXY)
    return master, job_id, lease_id, task


def test_the_proxy_task_says_where_to_send_the_report(harness):
    _, job_id, _, task = holder_with_proxy_lease(harness)
    assert task["media_info_upload_url"].endswith(f"/jobs/{job_id}/media-info")


def test_the_report_is_stored_without_location_and_fills_the_frame_rate(harness):
    master, job_id, lease_id, _ = holder_with_proxy_lease(harness)
    version_before = harness.job(job_id)["version"]

    response = put_media_info(harness, master, job_id, lease_id, ANDROID_REPORT)

    assert response.status_code == 204, response.text
    job = harness.job(job_id)
    stored = job["media_info"]
    # The device tags are there for the review page...
    assert stored["format"]["tags"]["com.android.model"] == "SM-S918B"
    # ...but the orchestrator removed the location the (misbehaving) worker
    # sent, and only remembers that the file had one.
    assert "location" not in stored["format"]["tags"]
    assert stored["location_present"] is True
    # Job.frame_rate comes from the same probe while it is still empty.
    assert job["frame_rate"] == 30.0
    # A machine upload: the review version does not move.
    assert job["version"] == version_before


def test_the_report_needs_the_make_proxy_lease(harness):
    master, job_id, _, _ = holder_with_proxy_lease(harness)
    response = put_media_info(harness, master, job_id, "not-a-lease", ANDROID_REPORT)
    # Same rule as the thumbnail: no live lease, no upload.
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "lease_lost"


def test_a_body_that_is_not_a_report_is_a_400(harness):
    master, job_id, lease_id, _ = holder_with_proxy_lease(harness)
    response = put_media_info(harness, master, job_id, lease_id, {"streams": "nope"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_error"


def test_a_huge_body_is_refused(harness):
    master, job_id, lease_id, _ = holder_with_proxy_lease(harness)
    huge = {"format": {"tags": {"comment": "x" * 600_000}}, "streams": []}
    response = put_media_info(harness, master, job_id, lease_id, huge)
    assert response.status_code == 413
