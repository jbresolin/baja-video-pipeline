"""The 480p preview of a delivered clip, for "Ver clipe" on the review UI (LIVESTACK-52).

Reviewers could not see what was rendered. So after a process task has
delivered its clip, and when the task gave a `preview_upload_url` (the
Settings switch "Gerar prévia dos clipes" is on), the worker makes a
preview of the clip with the review proxy's settings
(shared.ffmpeg.build_clip_preview_command) and uploads it, chunked and
resumable, with the Clip-SHA256 header naming the clip it shows.

It runs after the delivery, so the clip on air never waits for it, and
outside the lease (the delivery ended it): only a shutdown stops it. A
preview is nice to have: any failure is logged and the task still counts
as done; the review UI then says the preview did not arrive.
"""

import logging
from pathlib import Path

from shared.http_transfer import upload_file_in_chunks
from shared.models import ProcessTask
from shared.protocol import CLIP_SHA256_HEADER
from worker.leases import CancelToken
from worker.tasks.environment import TaskEnvironment

logger = logging.getLogger(__name__)

# Not worth insisting on: the next clip matters more.
PREVIEW_UPLOAD_ATTEMPTS = 3


def send_clip_preview(
    env: TaskEnvironment,
    task: ProcessTask,
    clip_path: Path,
    clip_sha256: str,
    cancel: CancelToken,
) -> bool:
    """Make and upload the preview; True when the VPS has it. Never raises."""
    if task.preview_upload_url is None:
        logger.info("no clip preview asked for (switched off in the Settings)")
        return False
    preview_path = env.storage.tmp_path(f"{task.job_id}.preview.mp4")
    after_delivery = cancel.shutdown_only()
    try:
        env.media.make_clip_preview(clip_path, preview_path, after_delivery)
        url = env.api.absolute_url(task.preview_upload_url)
        result = upload_file_in_chunks(
            env.transfer_client(after_delivery),
            url,
            preview_path,
            headers={**env.api.auth_headers_for(url), CLIP_SHA256_HEADER: clip_sha256},
            max_attempts=PREVIEW_UPLOAD_ATTEMPTS,
            chunk_size=env.settings.upload_chunk_bytes,
            retry_delay_s=env.settings.transfer_retry_delay_seconds,
            sleep=after_delivery.sleep,
        )
    except Exception as error:  # noqa: BLE001 - see the module docstring
        logger.warning("clip preview not sent (%s); the clip itself is delivered", error)
        return False
    finally:
        preview_path.unlink(missing_ok=True)
    logger.info("clip preview uploaded (%d bytes)", result.size_bytes)
    return True
