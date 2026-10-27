"""Peer file server (:8710): GET /raw/{job_id} with Range, GET /health (docs/api.md §7.2)."""

import httpx
import pytest

from shared.http_transfer import partial_path_for
from shared.testing.asgi_transport import SyncAsgiTransport
from worker.servers import build_peer_file_server_app
from worker.storage import Storage

RAW = bytes(range(256)) * 400  # 102,400 bytes


@pytest.fixture
def peer(tmp_path):
    storage = Storage(tmp_path / "peer", 10 * 1024**3, disk_free=lambda _: 500 * 1024**3)
    app = build_peer_file_server_app(storage, "peer-1", "worker")
    client = httpx.Client(transport=SyncAsgiTransport(app), base_url="http://peer.test:8710")
    return storage, client


def test_whole_raw_is_served(peer):
    storage, client = peer
    storage.raw_path("job-1", ".mp4").write_bytes(RAW)
    response = client.get("/raw/job-1")
    assert response.status_code == 200 and response.content == RAW


def test_range_request_gets_only_the_tail(peer):
    # A peer resuming a download asks for "bytes=1000-": it must get a 206
    # with exactly the bytes from 1000 on, and a Content-Range saying so.
    storage, client = peer
    storage.raw_path("job-1", ".mov").write_bytes(RAW)
    response = client.get("/raw/job-1", headers={"Range": "bytes=1000-"})
    assert response.status_code == 206
    assert response.content == RAW[1000:]
    assert response.headers["content-range"] == f"bytes 1000-{len(RAW) - 1}/{len(RAW)}"


def test_unknown_job_is_404(peer):
    _, client = peer
    response = client.get("/raw/nope")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_partial_download_is_never_served(peer):
    # Only the hidden partial file exists: we do not hold the raw yet.
    storage, client = peer
    partial_path_for(storage.raw_path("job-1", ".mp4")).write_bytes(RAW[:500])
    assert client.get("/raw/job-1").status_code == 404


def test_health_names_the_worker(peer):
    _, client = peer
    assert client.get("/health").json() == {"status": "ok", "worker_id": "peer-1", "role": "worker"}
