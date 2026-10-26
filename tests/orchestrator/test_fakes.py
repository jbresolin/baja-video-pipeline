"""Tests for the dev tools in orchestrator/fakes/.

Those scripts are not part of an installed package, so we load them by
file path with importlib (Python's machinery for importing a module from
a specific file).
"""

import importlib.util
from pathlib import Path

import httpx

from shared.models import EvolutionWebhook, ReceivedAs, find_incoming_video

FAKES_FOLDER = Path(__file__).resolve().parents[2] / "orchestrator" / "fakes"


def load_script(name: str):
    spec = importlib.util.spec_from_file_location(name, FAKES_FOLDER / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_fake_webhook_payload_is_a_valid_document_message(tmp_path):
    # Setup: a small file standing in for a video.
    video = tmp_path / "pass.mp4"
    video.write_bytes(b"x" * 1234)
    fake = load_script("fake_evolution_webhook")

    payload = fake.build_payload(video, "http://127.0.0.1:9/pass.mp4", False, "Ana")

    # The payload must parse with the same model the orchestrator uses, and
    # be recognised as a *document* with the file's real name and size.
    incoming = find_incoming_video(EvolutionWebhook.model_validate(payload).data.message)
    assert incoming.received_as == ReceivedAs.DOCUMENT
    assert incoming.media.file_name == "pass.mp4"
    assert incoming.media.file_length == 1234
    assert incoming.media_url == "http://127.0.0.1:9/pass.mp4"


def test_fake_webhook_can_send_as_video(tmp_path):
    # --as-video simulates a WhatsApp-recompressed video (→ low_res job).
    video = tmp_path / "pass.mp4"
    video.write_bytes(b"x")
    fake = load_script("fake_evolution_webhook")
    payload = fake.build_payload(video, "http://h/pass.mp4", True, "Ana")
    incoming = find_incoming_video(EvolutionWebhook.model_validate(payload).data.message)
    assert incoming.received_as == ReceivedAs.VIDEO


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
