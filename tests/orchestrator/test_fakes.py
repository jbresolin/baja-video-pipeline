"""Tests for the dev tools in orchestrator/fakes/.

Those scripts are not part of an installed package, so we load them by
file path with importlib (Python's machinery for importing a module from
a specific file).
"""

import importlib.util
from pathlib import Path

import httpx
import pytest

from shared.models import EvolutionWebhook, ReceivedAs, find_incoming_video
from shared.testing.whatsapp_media_encrypt import encrypt_file
from shared.whatsapp_media import download_incoming_media

FAKES_FOLDER = Path(__file__).resolve().parents[2] / "orchestrator" / "fakes"


def load_script(name: str):
    spec = importlib.util.spec_from_file_location(name, FAKES_FOLDER / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


FAKE_CDN = "http://127.0.0.1:8790"


def encrypted_fake_send(tmp_path, as_video: bool):
    """Do what the fake does before posting: encrypt a file, build the webhook.

    Returns (original bytes, encrypted blob bytes, parsed incoming media).
    """
    video = tmp_path / "pass.mp4"
    original = bytes(range(256)) * 50  # 12,800 bytes standing in for a video
    video.write_bytes(original)
    blob_path = tmp_path / "blob.enc"
    media_type = "video" if as_video else "document"
    encrypted = encrypt_file(video, blob_path, media_type)
    fake = load_script("fake_evolution_webhook")
    payload = fake.build_payload(video, encrypted, FAKE_CDN, "blob.enc", as_video, "Ana")
    incoming = find_incoming_video(EvolutionWebhook.model_validate(payload).data.message)
    return original, blob_path.read_bytes(), incoming


def test_fake_webhook_payload_is_a_valid_document_message(tmp_path):
    _, _, incoming = encrypted_fake_send(tmp_path, as_video=False)
    # The payload parses with the same model the orchestrator uses and is
    # a *document* with the file's real name, size and a CDN location.
    assert incoming.received_as == ReceivedAs.DOCUMENT
    assert incoming.media.file_name == "pass.mp4"
    assert incoming.media.file_length == 12_800
    assert incoming.media.url == f"{FAKE_CDN}/blob.enc"
    assert incoming.media.direct_path == "/blob.enc"
    assert incoming.missing_decryption_fields() == []


@pytest.mark.parametrize("as_video", [False, True])
def test_orchestrator_side_decrypts_what_the_fake_sent(tmp_path, as_video):
    # The real proof the dev tool is useful: the orchestrator's download
    # code (shared.whatsapp_media), configured to trust the fake CDN,
    # gets back the original file. A mock transport plays the fake CDN.
    original, blob, incoming = encrypted_fake_send(tmp_path, as_video)
    fake_cdn = httpx.MockTransport(lambda request: httpx.Response(200, content=blob))
    client = httpx.Client(transport=fake_cdn)

    result = download_incoming_media(client, incoming, tmp_path / "raw.mp4", base_url=FAKE_CDN)

    assert result.path.read_bytes() == original
    # --as-video gives a "video" message, which intake flags as low_res.
    expected_kind = ReceivedAs.VIDEO if as_video else ReceivedAs.DOCUMENT
    assert incoming.received_as == expected_kind


def test_fake_file_server_serves_the_file(tmp_path):
    # The fake starts a real HTTP server on 127.0.0.1 (this machine only)
    # so the orchestrator can download the "media". Port 0 asks the
    # operating system for any free port.
    (tmp_path / "pass.mp4").write_bytes(b"video bytes")
    fake = load_script("fake_evolution_webhook")
    server = fake.start_file_server(tmp_path, "127.0.0.1", 0)
    try:
        port = server.server_address[1]
        response = httpx.get(f"http://127.0.0.1:{port}/pass.mp4")
        assert response.content == b"video bytes"
    finally:
        server.shutdown()


def test_make_test_clip_parses_sizes():
    assert load_script("make_test_clip").parse_size("1920x1080") == (1920, 1080)
