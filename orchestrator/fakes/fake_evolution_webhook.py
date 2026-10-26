"""Dev tool: pretend a volunteer sent a video to the Baja Bot.

Serves a local file over HTTP (standing in for Evolution's media storage)
and posts a realistic Evolution "messages.upsert" webhook pointing at it to
a running orchestrator. Keeps serving for a while so the orchestrator can
download the file.

    uv run python orchestrator/fakes/fake_evolution_webhook.py clip.mp4
    uv run python orchestrator/fakes/fake_evolution_webhook.py clip.mp4 --as-video \\
        --orchestrator http://127.0.0.1:8000 --secret dev-secret

If the orchestrator runs in Docker, pass --media-host with an address the
container can reach (e.g. host.docker.internal).
"""

import argparse
import contextlib
import functools
import json
import sys
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote

import httpx

from shared.protocol import WEBHOOK_SECRET_HEADER
from shared.testing.evolution_payloads import document_message_payload, video_message_payload


def build_payload(video: Path, media_url: str, as_video: bool, sender_name: str) -> dict:
    """The webhook body for `video`, as a WhatsApp document (default) or video."""
    size = video.stat().st_size
    if as_video:
        return video_message_payload(media_url, file_length=size, sender_name=sender_name)
    return document_message_payload(
        media_url, file_name=video.name, file_length=size, sender_name=sender_name
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
    parser.add_argument("--bind", default="127.0.0.1", help="address the file server listens on")
    parser.add_argument("--media-host", default="127.0.0.1", help="host written in mediaUrl")
    parser.add_argument("--port", type=int, default=0, help="file server port (0 = any free)")
    parser.add_argument("--serve-seconds", type=int, default=120)
    arguments = parser.parse_args()

    video = arguments.video.resolve()
    if not video.is_file():
        print(f"no such file: {video}", file=sys.stderr)
        return 2

    server = start_file_server(video.parent, arguments.bind, arguments.port)
    port = server.server_address[1]
    media_url = f"http://{arguments.media_host}:{port}/{quote(video.name)}"
    payload = build_payload(video, media_url, arguments.as_video, arguments.sender_name)

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

    print(f"serving {media_url} for {arguments.serve_seconds}s (Ctrl+C to stop)")
    # Ctrl+C just ends the wait early; it is not an error.
    with contextlib.suppress(KeyboardInterrupt):
        time.sleep(arguments.serve_seconds)
    server.shutdown()
    return 0 if response.is_success else 1


if __name__ == "__main__":
    sys.exit(main())
