"""download_raw: fetch a raw, verify it, become one of its holders.

docs/api.md §5: GET `download_url` (Caddy inbox, with our bearer token),
else each of `fallback_download_urls` (peers on 8710, no token), with
shared.http_transfer.download_file (Range resume + SHA-256 check). Then
ffprobe it and POST /jobs/{id}/holder-confirm.

`download_url` itself may be a peer: the master is offered every job it
does not hold, also after the VPS deleted its copy (design §17, "master
backfill"), and then the only sources are other holders' file servers.

Crash safety: bytes go to the hidden "raws/.<job>.<ext>.part" and are
renamed only after the hash matched. That partial file is kept when the
task stops early for any reason (shutdown, crash, lost lease): if the
same job is offered again, the download resumes from it (design §17).
Partial files nobody resumed for 24 h are deleted at start (Storage).
"""

import logging
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from shared.hashing import sha256_of_file
from shared.http_transfer import (
    DownloadFailedError,
    HashMismatchError,
    download_file,
    partial_path_for,
)
from shared.models import DownloadRawTask, HolderConfirmRequest, MeasurementKind
from worker.api_client import ApiError
from worker.failures import (
    SourceHashMismatchError,
    TaskAbandonedError,
    TransferGaveUpError,
    UndecodableInputError,
    probe_input,
)
from worker.leases import ActiveLease, CancelToken
from worker.tasks.environment import TaskEnvironment

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
            info = probe_input(env.media, raw_path)
        except UndecodableInputError:
            # The bytes are exactly what was sent (hash matched), so the
            # file itself is not a usable video: keep nothing.
            raw_path.unlink(missing_ok=True)
            raise

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


def raw_destination_for(env: TaskEnvironment, sources: list[str], job_id: str) -> Path:
    """Where the raw goes: beside an earlier partial download if there is one.

    Resuming needs the same destination as last time, even when this offer
    lists other sources (first the inbox "….mp4", later only a peer
    "…/raw/<job>" with no extension). Otherwise the first source that
    names an extension decides, else ".bin" (ffmpeg reads the content, not
    the name).
    """
    earlier = env.storage.partial_raw_destination(job_id)
    if earlier is not None:
        return earlier
    for url in sources:
        extension = raw_extension_from(url)
        if extension != RAW_EXTENSION_FALLBACK:
            return env.storage.raw_path(job_id, extension)
    return env.storage.raw_path(job_id, RAW_EXTENSION_FALLBACK)


def _download_from_sources(
    env: TaskEnvironment, task: DownloadRawTask, cancel: CancelToken
) -> Path:
    sources = [task.download_url, *task.fallback_download_urls]
    # Every source writes to the same destination, so a partial file from
    # the inbox can be finished from a peer and vice versa.
    destination = raw_destination_for(env, sources, task.job_id)
    wrong_hash_sources = 0
    client = env.transfer_client(cancel)
    for url in sources:
        absolute_url = env.api.absolute_url(url)
        try:
            return _download_from_one_source(env, task, client, absolute_url, destination, cancel)
        except HashMismatchError as error:
            wrong_hash_sources += 1
            logger.warning("source %s sent a file with the wrong hash: %s", absolute_url, error)
        except (DownloadFailedError, httpx.HTTPError) as error:
            logger.warning("source %s failed: %s", absolute_url, error)

    if wrong_hash_sources == len(sources):
        raise SourceHashMismatchError(
            f"every source ({len(sources)}) sent a file whose SHA-256 is not {task.sha256}; "
            "the raw is probably corrupt at the source"
        )
    raise TransferGaveUpError(
        f"could not download the raw from any of its {len(sources)} source(s)"
    )


def _download_from_one_source(
    env: TaskEnvironment,
    task: DownloadRawTask,
    client: httpx.Client,
    url: str,
    destination: Path,
    cancel: CancelToken,
) -> Path:
    """Download (or resume) from `url`; raises what download_file raises.

    TaskCancelledError (lease lost, shutdown) passes straight through and
    leaves the partial file in place for a later resume.

    A wrong hash after *resuming* may be the fault of our old partial file
    (e.g. written from a source that changed since), not of this source.
    download_file has deleted that partial file by then, so one more try
    from byte 0 tells the two apart before we blame the source.
    """
    for attempt in ("resume", "from scratch"):
        had_partial_file = partial_path_for(destination).exists()
        logger.info("downloading raw from %s", url)
        started_at = time.monotonic()
        try:
            result = download_file(
                client,
                url,
                destination,
                task.sha256,
                headers=env.api.auth_headers_for(url),
                max_attempts=ATTEMPTS_PER_SOURCE,
                retry_delay_s=env.settings.transfer_retry_delay_seconds,
                sleep=cancel.sleep,
            )
        except HashMismatchError:
            if attempt == "resume" and had_partial_file:
                logger.warning("resumed download has the wrong hash; trying again from byte 0")
                continue
            raise
        env.measurements.record(
            MeasurementKind.DOWNLOAD, result.size_bytes - result.resumed_from_byte, started_at
        )
        logger.info(
            "raw downloaded: %d bytes (resumed from byte %d)",
            result.size_bytes,
            result.resumed_from_byte,
        )
        return result.path
    raise AssertionError("unreachable: the second attempt returns or raises")
