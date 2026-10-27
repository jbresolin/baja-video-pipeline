"""Dev mode only: the orchestrator stands in for Caddy's static file routes.

On the VPS, Caddy serves the bytes (design §6, §15):

    /inbox/*  /outbox/*    tailnet listener, after forward_auth → GET /auth/file
    /proxies/* /thumbs/*   public site, behind reviewer basic auth

A laptop has no Caddy, so with BVP_DEV_MODE=1 (which config.load_settings
only accepts with a localhost BVP_PUBLIC_BASE_URL) the orchestrator answers
the same paths itself, with the same rules. These functions only decide
*which file* may be sent; the route streams it with Range support.
"""

import re
import sqlite3
from pathlib import Path

from orchestrator.context import OrchestratorContext
from orchestrator.domain.file_access import authorize_file_request
from orchestrator.errors import not_found

# Same shapes the workers' uploads produce (storage.VpsLayout). Anything
# else (dot-files, *.part, "..", sub-folders) does not match and is a 404,
# like Caddy's `hide .* *.part *.part.json`.
PROXY_FILE_NAME = re.compile(r"^[0-9a-f]{32}\.mp4$")
THUMBNAIL_FILE_NAME = re.compile(r"^[0-9a-f]{32}\.jpg$")


def worker_file_path(
    context: OrchestratorContext,
    worker_row: sqlite3.Row,
    folder: str,
    file_name: str,
    method: str,
) -> Path:
    """The inbox/outbox file this worker may download, or raise 403/404.

    The permission check is exactly GET /auth/file's, applied to the path
    Caddy would have forwarded as X-Forwarded-Uri.
    """
    authorize_file_request(context, worker_row, f"/{folder}/{file_name}", method)
    folder_path = context.layout.inbox_dir if folder == "inbox" else context.layout.outbox_dir
    return _existing_file(folder_path / file_name)


def review_media_path(context: OrchestratorContext, folder: str, file_name: str) -> Path:
    """The proxy or thumbnail to show a reviewer, or raise 404."""
    if folder == "proxies":
        pattern, folder_path = PROXY_FILE_NAME, context.layout.proxies_dir
    else:
        pattern, folder_path = THUMBNAIL_FILE_NAME, context.layout.thumbs_dir
    if not pattern.match(file_name):
        raise not_found("file")
    return _existing_file(folder_path / file_name)


def _existing_file(path: Path) -> Path:
    if not path.is_file():
        raise not_found("file")
    return path
