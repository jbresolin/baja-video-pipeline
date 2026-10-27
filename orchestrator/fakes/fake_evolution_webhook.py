"""Dev tool: pretend a volunteer sent a video to the Baja Bot.

Encrypts a local file exactly as WhatsApp does, serves the encrypted blob
over HTTP (standing in for the WhatsApp CDN), and posts a realistic
Evolution "messages.upsert" webhook describing it to a running
orchestrator. Keeps serving for a while so the orchestrator can download.

    uv run python orchestrator/fakes/fake_evolution_webhook.py clip.mp4
    uv run python orchestrator/fakes/fake_evolution_webhook.py clip.mp4 --as-video \\
        --orchestrator http://127.0.0.1:8000 --secret dev-secret

The orchestrator only trusts media URLs under BVP_WHATSAPP_MEDIA_BASE_URL
(default https://mmg.whatsapp.net), so run it with
BVP_WHATSAPP_MEDIA_BASE_URL=http://127.0.0.1:8790 (this tool prints the
exact value). If the orchestrator runs in Docker, pass --media-host with an
address the container can reach (e.g. host.docker.internal).
"""

import argparse
import contextlib
import functools
import json
import sys
import tempfile
import threading
import time
import uuid
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx

from shared.protocol import WEBHOOK_SECRET_HEADER
from shared.testing.evolution_payloads import document_message_payload, video_message_payload
from shared.testing.whatsapp_media_encrypt import EncryptedMedia, encrypt_file

DEFAULT_FAKE_CDN_PORT = 8790


def build_payload(
    video: Path,
    encrypted: EncryptedMedia,
    media_base_url: str,
    blob_name: str,
    as_video: bool,
    sender_name: str,
) -> dict:
    """The webhook body for an encrypted `video`, as a WhatsApp document or video."""
    direct_path = f"/{blob_name}"
    url = media_base_url.rstrip("/") + direct_path
    if as_video:
        return video_message_payload(encrypted, url, direct_path, sender_name=sender_name)
    return document_message_payload(
        encrypted, url, direct_path, file_name=video.name, sender_name=sender_name
    )


def start_file_server(folder: Path, host: str, port: int) -> ThreadingHTTPServer:
    """Serve `folder` over HTTP in a background thread; returns the server."""
    handler = functools.partial(SimpleHTTPRequestHandler, directory=str(folder))
    server = ThreadingHTTPServer((host, port), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def main() -> int:
    parser = argparse.ArgumentParser(description="Post a fake Evolution webhook for a local file.")
    parser.add_argument("video", type=Path, help="video file to 'send'")
    parser.add_argument("--orchestrator", default="http://127.0.0.1:8000")
    parser.add_argument("--secret", default="dev-secret", help=f"value of {WEBHOOK_SECRET_HEADER}")
    parser.add_argument("--as-video", action="store_true", help="send as WhatsApp video (low_res)")
    parser.add_argument("--sender-name", default="Voluntário Teste")
    parser.add_argument("--bind", default="127.0.0.1", help="address the fake CDN listens on")
    parser.add_argument("--media-host", default="127.0.0.1", help="host written in the webhook")
    parser.add_argument("--port", type=int, default=DEFAULT_FAKE_CDN_PORT, help="fake CDN port")
    parser.add_argument("--serve-seconds", type=int, default=120)
    arguments = parser.parse_args()

    video = arguments.video.resolve()
    if not video.is_file():
        print(f"no such file: {video}", file=sys.stderr)
        return 2

    with tempfile.TemporaryDirectory(prefix="fake-whatsapp-cdn-") as cdn_folder:
        blob_name = f"{uuid.uuid4().hex}.enc"
        media_type = "video" if arguments.as_video else "document"
        encrypted = encrypt_file(video, Path(cdn_folder) / blob_name, media_type)

        server = start_file_server(Path(cdn_folder), arguments.bind, arguments.port)
        media_base_url = f"http://{arguments.media_host}:{server.server_address[1]}"
        print(f"orchestrator needs BVP_WHATSAPP_MEDIA_BASE_URL={media_base_url}")
        payload = build_payload(
            video, encrypted, media_base_url, blob_name, arguments.as_video, arguments.sender_name
        )

        response = httpx.post(
            f"{arguments.orchestrator}/intake/evolution-webhook",
            json=payload,
            headers={WEBHOOK_SECRET_HEADER: arguments.secret},
            timeout=60,
        )
        print(f"HTTP {response.status_code}")
        try:
            print(json.dumps(response.json(), indent=2, ensure_ascii=False))
        except ValueError:
            print(response.text)

        print(f"serving {media_base_url}/{blob_name} for {arguments.serve_seconds}s (Ctrl+C stops)")
        # Ctrl+C just ends the wait early; it is not an error.
        with contextlib.suppress(KeyboardInterrupt):
            time.sleep(arguments.serve_seconds)
        server.shutdown()
    return 0 if response.is_success else 1


if __name__ == "__main__":
    sys.exit(main())
