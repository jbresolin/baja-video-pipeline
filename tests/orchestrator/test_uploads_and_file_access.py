"""Tests for worker uploads (thumbnail, chunked proxy and clip) and GET /auth/file."""

import hashlib

import pytest

from orchestrator.testing.scenarios import (
    SAMPLE_JPEG,
    SAMPLE_PROXY,
    claim_ok,
    hold_raw,
    receive_job,
    upload_in_chunks,
)
from shared.models import TaskKind, WorkerRole
from shared.protocol import (
    LEASE_ID_HEADER,
    UPLOAD_LENGTH_HEADER,
    UPLOAD_OFFSET_HEADER,
    UPLOAD_SHA256_HEADER,
)


@pytest.fixture
def proxy_ready_to_upload(harness):
    """A job held by the master, with the master holding the make_proxy lease."""
    master = harness.enroll(WorkerRole.MASTER, "master")
    job_id = receive_job(harness)
    hold_raw(harness, master, job_id)
    lease_id, _ = claim_ok(harness, master, job_id, TaskKind.MAKE_PROXY)
    return master, job_id, lease_id


def test_chunked_proxy_upload_moves_the_job_to_awaiting_review(harness, proxy_ready_to_upload):
    master, job_id, lease_id = proxy_ready_to_upload
    thumbnail = harness.client.put(
        f"/jobs/{job_id}/thumbnail",
        content=SAMPLE_JPEG,
        headers={**master.headers, LEASE_ID_HEADER: lease_id},
    )
    assert thumbnail.status_code == 204
    # 6000 bytes in chunks of 1000: six requests, only the last is complete.
    last = upload_in_chunks(
        harness, master, f"/jobs/{job_id}/proxy", lease_id, SAMPLE_PROXY, chunk_size=1000
    )
    assert last.json() == {"received_bytes": len(SAMPLE_PROXY), "complete": True}
    job = harness.job(job_id)
    assert job["state"] == "awaiting_review"
    assert job["proxy_available"] is True
    assert job["thumbnail_url"] == f"/thumbs/{job_id}.jpg"
    # The files are where Caddy serves them from.
    assert harness.context.layout.proxy_path(job_id).read_bytes() == SAMPLE_PROXY
    assert harness.context.layout.thumbnail_path(job_id).read_bytes() == SAMPLE_JPEG
    # The make_proxy lease ended with the upload.
    assert harness.job(job_id)["leases"] == []


def test_a_chunk_at_the_wrong_offset_is_told_where_to_continue(harness, proxy_ready_to_upload):
    master, job_id, lease_id = proxy_ready_to_upload
    headers = {
        **master.headers,
        UPLOAD_LENGTH_HEADER: "100",
        UPLOAD_SHA256_HEADER: hashlib.sha256(b"a" * 100).hexdigest(),
        LEASE_ID_HEADER: lease_id,
    }
    harness.client.post(
        f"/jobs/{job_id}/proxy", content=b"a" * 40, headers={**headers, UPLOAD_OFFSET_HEADER: "0"}
    )
    # Pretend the worker crashed and restarts from 0 with the next part.
    wrong = harness.client.post(
        f"/jobs/{job_id}/proxy", content=b"a" * 60, headers={**headers, UPLOAD_OFFSET_HEADER: "0"}
    )
    assert wrong.status_code == 409
    assert wrong.json()["error"]["code"] == "upload_offset_mismatch"
    assert wrong.json()["error"]["details"]["expected_offset"] == 40


def test_uploads_need_the_right_lease(harness, proxy_ready_to_upload):
    master, job_id, _ = proxy_ready_to_upload
    response = upload_in_chunks(harness, master, f"/jobs/{job_id}/proxy", "not-a-lease", b"x")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "lease_lost"


def test_a_thumbnail_that_is_not_a_jpeg_is_refused(harness, proxy_ready_to_upload):
    master, job_id, lease_id = proxy_ready_to_upload
    response = harness.client.put(
        f"/jobs/{job_id}/thumbnail",
        content=b"\x89PNG not a jpeg",
        headers={**master.headers, LEASE_ID_HEADER: lease_id},
    )
    assert response.status_code == 400


def auth_file(harness, worker, uri, method="GET"):
    """Ask /auth/file the way Caddy's forward_auth does."""
    return harness.client.get(
        "/auth/file",
        headers={**worker.headers, "X-Forwarded-Uri": uri, "X-Forwarded-Method": method},
    )


def test_auth_file_allows_the_inbox_only_under_a_download_lease(harness):
    master = harness.enroll(WorkerRole.MASTER, "master")
    other = harness.enroll(WorkerRole.WORKER, "other")
    job_id = receive_job(harness)
    uri = f"/inbox/{job_id}.mp4"
    # No lease yet: refused.
    assert auth_file(harness, master, uri).status_code == 403
    claim_ok(harness, master, job_id, TaskKind.DOWNLOAD_RAW)
    allowed = auth_file(harness, master, uri)
    assert allowed.status_code == 204
    # Caddy can log who downloaded it.
    assert allowed.headers["X-Worker-Id"] == master.worker_id
    # HEAD is fine too; anything else is not.
    assert auth_file(harness, master, uri, "HEAD").status_code == 204
    assert auth_file(harness, master, uri, "DELETE").status_code == 403
    # Another worker without a lease is still refused.
    assert auth_file(harness, other, uri).status_code == 403


@pytest.mark.parametrize(
    "uri",
    [
        "/inbox/../db/bvp.sqlite3",  # escaping the folder
        "/inbox/.hidden.mp4.part",  # a half-written dot-file
        "/inbox/%2e%2e/db/bvp.sqlite3",  # the same escape, URL-encoded
        "/proxies/abc.mp4",  # not a worker path at all
        "/db/bvp.sqlite3",
    ],
)
def test_auth_file_refuses_anything_that_is_not_a_plain_job_file(harness, uri):
    master = harness.enroll(WorkerRole.MASTER, "master")
    assert auth_file(harness, master, uri).status_code == 403


def test_auth_file_outbox_is_for_the_studio_only(harness):
    studio = harness.enroll(WorkerRole.STUDIO, "studio")
    master = harness.enroll(WorkerRole.MASTER, "master")
    uri = f"/outbox/{'a' * 32}.mp4"
    assert auth_file(harness, studio, uri).status_code == 204
    assert auth_file(harness, master, uri).status_code == 403


def test_auth_file_without_a_token_is_401(harness):
    response = harness.client.get("/auth/file", headers={"X-Forwarded-Uri": "/inbox/x.mp4"})
    assert response.status_code == 401
