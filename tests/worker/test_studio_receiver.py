"""Studio receiver (:8720), latest.m3u and the delivered ACK (docs/api.md §7.2, §8.1).

The receiver app is called in memory through an httpx client. Pushes use
the real client helper (shared.http_transfer.upload_file_in_chunks), so the
tests prove that a worker's push and the studio's receiver agree.
"""

import threading

import httpx
import pytest

from shared.hashing import sha256_of_bytes
from shared.http_transfer import HashMismatchError, UploadFailedError, upload_file_in_chunks
from shared.layout import LATEST_PLAYLIST_NAME
from shared.testing.asgi_transport import SyncAsgiTransport
from tests.worker.conftest import make_api_client
from tests.worker.fake_orchestrator import BASE_URL, FakeOrchestrator
from tests.worker.fakes import RoutingTransport
from worker.studio.acknowledger import DeliveryAcknowledger
from worker.studio.library import StudioLibrary
from worker.studio.receiver_app import build_receiver_app


@pytest.fixture
def studio(tmp_path):
    """A studio: box root, fake orchestrator for ACKs, receiver client."""
    orchestrator = FakeOrchestrator(tmp_path / "vps")
    network = RoutingTransport()
    network.add_app(BASE_URL, orchestrator.app)
    library = StudioLibrary(tmp_path / "box")
    acknowledger = DeliveryAcknowledger(
        make_api_client(network), library, threading.Event(), retry_delay_seconds=0
    )
    app = build_receiver_app(library, acknowledger, "studio-1")
    client = httpx.Client(transport=SyncAsgiTransport(app), base_url="http://studio.test:8720")
    return orchestrator, library, acknowledger, client


def push(client, tmp_path, content: bytes, relative_path: str, job_id: str = "job-1"):
    """Push `content` the way a worker does, in 1 KB chunks."""
    source = tmp_path / f"{job_id}-upload.mp4"
    source.write_bytes(content)
    return upload_file_in_chunks(
        client,
        f"/receive/{job_id}",
        source,
        headers={"Clip-Relative-Path": relative_path},
        chunk_size=1024,
    )


@pytest.mark.parametrize(
    "bad_path",
    [
        "../outside.mp4",  # climbs out of the box root
        "enduro/../../x.mp4",  # climbs out in the middle
        "/etc/clip.mp4",  # absolute path
        "C:\\\\Windows\\\\clip.mp4",  # Windows path with backslashes
        "enduro/42/3.exe",  # not an .mp4
        "",  # no header value at all
    ],
)
def test_unsafe_clip_paths_are_refused(studio, tmp_path, bad_path):
    orchestrator, library, acknowledger, client = studio
    response = client.post(
        "/receive/job-1",
        content=b"x",
        headers={
            "Clip-Relative-Path": bad_path,
            "Upload-Offset": "0",
            "Upload-Length": "1",
            "Upload-SHA256": sha256_of_bytes(b"x"),
        },
    )
    # 400 validation_error, as the contract says, and nothing was written
    # anywhere (not even a partial file) and nothing queued for an ACK.
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_error"
    assert [p for p in tmp_path.rglob("*") if p.is_file()] == []
    assert acknowledger.pending_count() == 0


def test_pushed_clip_lands_at_its_path_and_is_acked(studio, tmp_path):
    orchestrator, library, acknowledger, client = studio
    job = orchestrator.add_job(
        b"raw", prova="Enduro", car_number=42, team_name="Equipe", passagem=3
    )
    clip = b"clip bytes " * 500

    push(client, tmp_path, clip, "enduro/42/3.mp4", job_id=job.job_id)

    # The clip is at <box root>/enduro/42/3.mp4 and no partial files remain
    # (they are hidden dot-files while the upload runs).
    assert (library.root / "enduro/42/3.mp4").read_bytes() == clip
    assert [p.name for p in (library.root / "enduro/42").iterdir()] == ["3.mp4"]
    # The ACK runs in the background in real life; here we run it now.
    acknowledger.handle_all_pending()
    [(acked_job, delivered)] = orchestrator.delivered
    assert acked_job == job.job_id
    assert delivered.sha256 == sha256_of_bytes(clip)
    assert delivered.size_bytes == len(clip)
    assert delivered.relative_path == "enduro/42/3.mp4"
    # The playlist title comes from the job (prova name, team).
    playlist = (library.root / LATEST_PLAYLIST_NAME).read_text()
    assert (
        playlist == "#EXTM3U\n#EXTINF:-1,Enduro - carro 42 Equipe - passagem 3\nenduro/42/3.mp4\n"
    )


def test_ack_is_retried_until_the_orchestrator_accepts_it(studio, tmp_path):
    orchestrator, library, acknowledger, client = studio
    orchestrator.delivered_calls_to_fail = 2  # two 503s, then OK
    push(client, tmp_path, b"clip", "enduro/1/1.mp4")

    acknowledger.handle_all_pending()  # 1st try: 503, stays queued
    acknowledger.handle_all_pending()  # 2nd try: 503, stays queued
    assert orchestrator.delivered == [] and acknowledger.pending_count() == 1
    acknowledger.handle_all_pending()  # 3rd try: accepted

    assert len(orchestrator.delivered) == 1 and acknowledger.pending_count() == 0
    # Retrying did not add the clip to the playlist three times.
    assert (library.root / LATEST_PLAYLIST_NAME).read_text().count("enduro/1/1.mp4") == 1


def test_playlist_is_newest_first_capped_and_reprocessed_clips_move_to_top(tmp_path):
    # Directly on the library: 52 clips arrive one after another.
    library = StudioLibrary(tmp_path / "box")
    for car in range(1, 53):
        library.record_clip(f"enduro/{car}/1.mp4", job=None)

    lines = (library.root / LATEST_PLAYLIST_NAME).read_text().splitlines()
    paths = [line for line in lines if not line.startswith("#")]
    # At most 50 entries, newest (car 52) first, oldest two dropped.
    assert len(paths) == 50
    assert paths[0] == "enduro/52/1.mp4" and paths[-1] == "enduro/3/1.mp4"
    # Without job details, the title is rebuilt from the path.
    assert lines[1] == "#EXTINF:-1,Enduro - carro 52 - passagem 1"

    # Car 10's clip is reprocessed: same path, now at the top, listed once.
    library.record_clip("enduro/10/1.mp4", job=None)
    paths = [line for line in (library.root / LATEST_PLAYLIST_NAME).read_text().splitlines()
             if not line.startswith("#")]  # fmt: skip
    assert paths[0] == "enduro/10/1.mp4"
    assert paths.count("enduro/10/1.mp4") == 1


def test_corrupted_push_is_refused_and_leaves_no_file(studio, tmp_path):
    orchestrator, library, acknowledger, client = studio
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"real clip content")
    # The worker claims a hash that does not match the bytes: the
    # receiver must refuse at the end and keep nothing.
    with pytest.raises(HashMismatchError):
        upload_file_in_chunks(
            client,
            "/receive/job-1",
            source,
            sha256="0" * 64,
            headers={"Clip-Relative-Path": "enduro/1/1.mp4"},
        )
    assert not (library.root / "enduro/1/1.mp4").exists()
    assert acknowledger.pending_count() == 0


def test_health(studio):
    _, _, _, client = studio
    assert client.get("/health").json() == {
        "status": "ok",
        "worker_id": "studio-1",
        "role": "studio",
    }


def test_missing_upload_headers_are_a_validation_error(studio):
    _, _, _, client = studio
    response = client.post(
        "/receive/job-1", content=b"x", headers={"Clip-Relative-Path": "a/1/1.mp4"}
    )
    assert response.status_code == 400


def test_push_helper_raises_on_refusal(studio, tmp_path):
    # A worker pushing with a bad path gets UploadFailedError (and then
    # uses the outbox), carrying the status code.
    _, _, _, client = studio
    with pytest.raises(UploadFailedError) as raised:
        push(client, tmp_path, b"clip", "../x.mp4")
    assert raised.value.status_code == 400
