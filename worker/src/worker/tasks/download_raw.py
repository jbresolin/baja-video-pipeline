"""download_raw: fetch a raw, verify it, become one of its holders.

docs/api.md §5: GET `download_url` (Caddy inbox, with our bearer token),
else each of `fallback_download_urls` (peers on 8710, no token), with
shared.http_transfer.download_file (Range resume + SHA-256 check). Then
ffprobe it and POST /jobs/{id}/holder-confirm.

Crash safety: bytes go to the hidden "raws/.<job>.<ext>.part" and are
renamed only after the hash matched. After a crash or restart the same
task resumes from that partial file. When the lease is lost the partial
file is thrown away (docs/api.md §5); on shutdown it is kept.
"""

import logging
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from shared.hashing import sha256_of_file
from shared.http_transfer import DownloadFailedError, HashMismatchError, download_file
from shared.models import DownloadRawTask, HolderConfirmRequest, MeasurementKind
from worker.api_client import ApiError
from worker.leases import ActiveLease, CancelToken, TaskCancelledError
from worker.media_tools import MediaToolError
from worker.tasks.environment import TaskAbandonedError, TaskEnvironment, TaskFailedError

logger = logging.getLogger(__name__)

# Per source URL; download_file resumes between attempts.
ATTEMPTS_PER_SOURCE = 3
RAW_EXTENSION_FALLBACK = ".bin"


def raw_extension_from(url_or_name: str | None) -> str:
    """ ".mp4" from ".../inbox/abc.MP4" or "VID_01.mov"; ".bin" when unknown.

    Peer URLs (".../raw/<job_id>") have no extension, hence the fallback.
    """
    if not url_or_name:
        return RAW_EXTENSION_FALLBACK
    suffix = Path(urlsplit(url_or_name).path).suffix.lower()
    if 2 <= len(suffix) <= 6 and suffix[1:].isalnum():
        return suffix
    return RAW_EXTENSION_FALLBACK


def run_download_raw(
    env: TaskEnvironment, lease: ActiveLease, task: DownloadRawTask, cancel: CancelToken
) -> None:
    with env.storage.pin(task.job_id):
        raw_path = _already_held_raw(env, task) or _download_from_sources(env, task, cancel)
        cancel.raise_if_cancelled()
        try:
            info = env.media.probe(raw_path)
        except MediaToolError as error:
            # The bytes are exactly what was sent (hash matched): the file
            # itself is not a usable video. Keep nothing, tell a human.
            raw_path.unlink(missing_ok=True)
            message = f"downloaded raw is not a readable video: {error.message}"
            raise TaskFailedError(message) from error

        confirm = HolderConfirmRequest(
            lease_id=lease.lease_id,
            sha256=task.sha256,
            size_bytes=raw_path.stat().st_size,
            duration_s=info.duration_s,
            has_audio=info.has_audio,
        )
        try:
            answer = env.report(
                lambda: env.api.holder_confirm(task.job_id, confirm), cancel, "holder-confirm"
            )
        except ApiError as error:
            if error.code == "hash_mismatch":
                # We are dropped as holder and must delete our copy.
                raw_path.unlink(missing_ok=True)
                raise TaskAbandonedError("orchestrator rejected our hash; copy deleted") from error
            raise
        logger.info(
            "holder confirmed (%d holder(s); VPS copy deleted: %s)",
            answer.confirmed_holder_count,
            answer.raw_deleted_from_vps,
        )


def _already_held_raw(env: TaskEnvironment, task: DownloadRawTask) -> Path | None:
    """A complete raw from an earlier run (e.g. crash before holder-confirm)."""
    existing = env.storage.find_raw(task.job_id)
    if existing is None:
        return None
    if sha256_of_file(existing) == task.sha256:
        logger.info("raw already on disk and verified; skipping the download")
        return existing
    logger.warning("raw on disk has the wrong SHA-256; downloading it again")
    existing.unlink()
    return None


def _download_from_sources(
    env: TaskEnvironment, task: DownloadRawTask, cancel: CancelToken
) -> Path:
    # Every source writes to the same destination, so a partial file from
    # the inbox can be finished from a peer and vice versa.
    destination = env.storage.raw_path(task.job_id, raw_extension_from(task.download_url))
    sources = [task.download_url, *task.fallback_download_urls]
    wrong_hash_sources = 0
    client = env.transfer_client(cancel)
    for url in sources:
        absolute_url = env.api.absolute_url(url)
        headers = env.api.auth_headers() if env.api.is_orchestrator_url(absolute_url) else {}
        logger.info("downloading raw from %s", absolute_url)
        started_at = time.monotonic()
        try:
            result = download_file(
                client,
                absolute_url,
                destination,
                task.sha256,
                headers=headers,
                max_attempts=ATTEMPTS_PER_SOURCE,
                sleep=cancel.sleep,
            )
        except TaskCancelledError as cancelled:
            if cancelled.lease_lost:
                env.storage.delete_partial_raws(task.job_id)
            raise
        except HashMismatchError as error:
            wrong_hash_sources += 1
            logger.warning("source %s sent a file with the wrong hash: %s", absolute_url, error)
            continue
        except (DownloadFailedError, httpx.HTTPError) as error:
            logger.warning("source %s failed: %s", absolute_url, error)
            continue
        env.measurements.record(
            MeasurementKind.DOWNLOAD, result.size_bytes - result.resumed_from_byte, started_at
        )
        logger.info(
            "raw downloaded: %d bytes (resumed from byte %d)",
            result.size_bytes,
            result.resumed_from_byte,
        )
        return result.path

    if wrong_hash_sources == len(sources):
        raise TaskFailedError(
            f"every source ({len(sources)}) sent a file whose SHA-256 is not {task.sha256}; "
            "the raw is probably corrupt on the server"
        )
    raise TaskAbandonedError("no download source is reachable right now")
