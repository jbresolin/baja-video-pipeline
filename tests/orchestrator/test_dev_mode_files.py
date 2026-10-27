"""Tests for dev mode's file routes: the orchestrator standing in for Caddy.

On the VPS, Caddy serves /inbox/*, /outbox/*, /proxies/* and /thumbs/*.
On a laptop there is no Caddy, so with BVP_DEV_MODE=1 the orchestrator
serves the same paths itself (design §18). These tests check three things:

1. the same permission rules as GET /auth/file (inbox: only the worker
   holding the download_raw lease; outbox: only the studio) and as Caddy's
   reviewer basic auth (proxies, thumbnails);
2. Range requests work, so a broken download resumes where it stopped;
3. none of this can be switched on in production: the routes do not exist
   without dev mode, and dev mode refuses to start with a public URL.
"""

import pytest

from orchestrator.config import ConfigError, load_settings
from orchestrator.testing.harness import build_harness
from orchestrator.testing.scenarios import (
    SAMPLE_JPEG,
    SAMPLE_PROXY,
    SAMPLE_VIDEO_BYTES,
    claim_ok,
    hold_raw,
    make_proxy,
    path_of,
    receive_job,
)
from shared.models import TaskKind, WorkerRole


@pytest.fixture
def dev_harness(tmp_path):
    # A whole orchestrator, like the `harness` fixture, but started with
    # BVP_DEV_MODE=1 (the harness then uses http://127.0.0.1:8000 as its
    # public URL, the only kind dev mode accepts).
    built = build_harness(tmp_path / "data", dev_mode=True)
    yield built
    built.client.close()
    built.context.database.close()


def test_the_worker_with_the_download_lease_gets_the_raw(dev_harness):
    # Set up: a video arrives and the master claims its download_raw task.
    # The task's download_url points at /inbox/<job>.<ext>.
    master = dev_harness.enroll(WorkerRole.MASTER, "master")
    job_id = receive_job(dev_harness)
    _, task = claim_ok(dev_harness, master, job_id, TaskKind.DOWNLOAD_RAW)

    # Act: fetch that URL from the orchestrator itself (no Caddy in between).
    response = dev_harness.client.get(path_of(task["download_url"]), headers=master.headers)

    # The bytes are exactly the raw in the inbox, and the response names the
    # worker the way Caddy's access log would.
    assert response.status_code == 200
    assert response.content == SAMPLE_VIDEO_BYTES
    assert response.headers["X-Worker-Id"] == master.worker_id


def test_a_range_request_resumes_a_download_in_the_middle(dev_harness):
    # Same setup as above.
    master = dev_harness.enroll(WorkerRole.MASTER, "master")
    job_id = receive_job(dev_harness)
    _, task = claim_ok(dev_harness, master, job_id, TaskKind.DOWNLOAD_RAW)

    # Act: ask only for byte 1000 to the end, as a worker does after a crash.
    response = dev_harness.client.get(
        path_of(task["download_url"]), headers={**master.headers, "Range": "bytes=1000-"}
    )

    # 206 Partial Content with just the missing tail proves Range works:
    # the worker can glue it to the 1000 bytes it already had.
    assert response.status_code == 206
    assert response.content == SAMPLE_VIDEO_BYTES[1000:]
    assert response.headers["Content-Range"] == (
        f"bytes 1000-{len(SAMPLE_VIDEO_BYTES) - 1}/{len(SAMPLE_VIDEO_BYTES)}"
    )


def test_head_reports_the_size_without_the_body(dev_harness):
    master = dev_harness.enroll(WorkerRole.MASTER, "master")
    job_id = receive_job(dev_harness)
    _, task = claim_ok(dev_harness, master, job_id, TaskKind.DOWNLOAD_RAW)

    response = dev_harness.client.head(path_of(task["download_url"]), headers=master.headers)

    # HEAD answers with the length, but sends no bytes.
    assert response.status_code == 200
    assert int(response.headers["Content-Length"]) == len(SAMPLE_VIDEO_BYTES)
    assert response.content == b""


def test_a_worker_without_the_lease_is_refused_like_auth_file_refuses_it(dev_harness):
    # Set up: the master claims the download; a second worker has no lease.
    master = dev_harness.enroll(WorkerRole.MASTER, "master")
    other = dev_harness.enroll(WorkerRole.WORKER, "other")
    job_id = receive_job(dev_harness)
    _, task = claim_ok(dev_harness, master, job_id, TaskKind.DOWNLOAD_RAW)
    path = path_of(task["download_url"])

    # Act: the other worker, and someone with no token at all, ask for it.
    other_response = dev_harness.client.get(path, headers=other.headers)
    anonymous = dev_harness.client.get(path)

    # 403 / 401: exactly what GET /auth/file answers Caddy in production.
    assert other_response.status_code == 403
    assert anonymous.status_code == 401


def test_only_the_studio_may_download_outbox_clips(dev_harness):
    # Set up: put a clip straight into the outbox folder (how it got there
    # does not matter for this rule), plus a studio and a normal worker.
    studio = dev_harness.enroll(WorkerRole.STUDIO, "box")
    worker = dev_harness.enroll(WorkerRole.WORKER, "pc")
    job_id = "a" * 32
    dev_harness.context.layout.outbox_path(job_id).write_bytes(b"clip")

    by_studio = dev_harness.client.get(f"/outbox/{job_id}.mp4", headers=studio.headers)
    by_worker = dev_harness.client.get(f"/outbox/{job_id}.mp4", headers=worker.headers)

    assert by_studio.status_code == 200 and by_studio.content == b"clip"
    assert by_worker.status_code == 403


def test_half_written_and_hidden_files_are_never_served(dev_harness):
    # Writers create "<name>.part" and dot-files first and rename them when
    # complete (design §15). Caddy hides those names; dev mode must too.
    master = dev_harness.enroll(WorkerRole.MASTER, "master")
    job_id = receive_job(dev_harness)
    dev_harness.context.layout.proxies_dir.joinpath(f".{job_id}.mp4.part").write_bytes(b"x")

    # A reviewer asks for the hidden proxy, the master for a hidden inbox file.
    proxy = dev_harness.client.get(f"/proxies/.{job_id}.mp4.part", auth=dev_harness.reviewer_auth)
    inbox = dev_harness.client.get(f"/inbox/.{job_id}.bin.part", headers=master.headers)

    # The credentials were fine; the names alone are refused (404 / 403),
    # and no bytes come back.
    assert proxy.status_code == 404
    assert inbox.status_code == 403


def test_reviewers_get_proxies_and_thumbnails_with_basic_auth(dev_harness):
    # Set up: a job goes all the way to awaiting_review, which uploads a
    # proxy and a thumbnail to the "VPS".
    master = dev_harness.enroll(WorkerRole.MASTER, "master")
    job_id = receive_job(dev_harness)
    hold_raw(dev_harness, master, job_id)
    make_proxy(dev_harness, master, job_id)

    # Act: a reviewer opens the paths the review UI links to.
    job = dev_harness.job(job_id)
    proxy = dev_harness.client.get(job["proxy_url"], auth=dev_harness.reviewer_auth)
    thumbnail = dev_harness.client.get(job["thumbnail_url"], auth=dev_harness.reviewer_auth)
    seek = dev_harness.client.get(
        job["proxy_url"], auth=dev_harness.reviewer_auth, headers={"Range": "bytes=0-99"}
    )
    anonymous = dev_harness.client.get(job["proxy_url"])

    assert proxy.status_code == 200 and proxy.content == SAMPLE_PROXY
    assert proxy.headers["Content-Type"] == "video/mp4"
    assert thumbnail.status_code == 200 and thumbnail.content == SAMPLE_JPEG
    # The player seeks with Range requests.
    assert seek.status_code == 206 and seek.content == SAMPLE_PROXY[:100]
    # Like Caddy's basic_auth on /proxies/*: no reviewer, no video.
    assert anonymous.status_code == 401


def test_without_dev_mode_the_file_routes_do_not_exist(harness):
    # The normal `harness` runs WITHOUT dev mode, like production. Even the
    # worker holding the download lease gets a plain 404: the bytes only
    # ever come from Caddy there.
    master = harness.enroll(WorkerRole.MASTER, "master")
    job_id = receive_job(harness)
    _, task = claim_ok(harness, master, job_id, TaskKind.DOWNLOAD_RAW)

    response = harness.client.get(path_of(task["download_url"]), headers=master.headers)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_dev_mode_does_not_appear_in_the_api_document(dev_harness):
    # The dev routes are Caddy's paths, not part of the orchestrator's
    # contract, so the service's OpenAPI document must stay the same with
    # or without dev mode (tests/orchestrator/test_api_contract.py compares it).
    paths = dev_harness.client.get("/openapi.json").json()["paths"]
    assert not any(path.startswith(("/inbox", "/proxies", "/thumbs")) for path in paths)


@pytest.mark.parametrize(
    "public_url",
    ["https://review.bajasae.example", "http://100.64.0.1:8080", "http://0.0.0.0:8000"],
)
def test_dev_mode_refuses_to_start_with_a_non_local_public_url(tmp_path, public_url):
    # The VPS always has a real domain in BVP_PUBLIC_BASE_URL. If someone
    # leaves BVP_DEV_MODE=1 in orchestrator.env there, the orchestrator must
    # refuse to start rather than serve raws without Caddy's checks.
    with pytest.raises(ConfigError, match="BVP_DEV_MODE"):
        build_harness(tmp_path, dev_mode=True, public_base_url=public_url)


@pytest.mark.parametrize(
    "public_url", ["http://127.0.0.1:8000", "http://localhost:8000", "http://[::1]:8000"]
)
def test_dev_mode_starts_with_a_localhost_public_url(public_url):
    # The three spellings of "this machine" are all accepted.
    settings = load_settings(
        admin_token="a",
        webhook_secret="w",
        public_base_url=public_url,
        tailnet_base_url=public_url,
        evolution_api_key="k",
        tailscale_oauth_client_id="id",
        tailscale_oauth_client_secret="s",
        dev_mode=True,
    )
    assert settings.dev_mode is True
