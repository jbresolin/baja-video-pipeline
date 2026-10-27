"""process: render the final clip from a held raw, then deliver it.

Render (docs/api.md §5, contract §30): trim to the reviewer's cuts, draw
the overlay PNG bottom-left, loudnorm, tone-map HDR. Delivery is in
worker.tasks.clip_delivery (direct push to the studio, else the outbox).

The finished clip waits in clips/<job_id>.mp4 next to a small JSON
"sidecar" naming the inputs it was rendered from. If the worker crashes
after rendering and is offered the same job again with the same inputs,
it skips the render and resumes the upload. A lost lease (the reviewer
changed the cut, or the lease expired) deletes the clip.
"""

import json
import logging
import time
from pathlib import Path

from shared.models import MeasurementKind, ProcessTask
from worker.leases import ActiveLease, CancelToken, TaskCancelledError
from worker.media_tools import MediaToolError
from worker.overlay import OverlayText, final_frame_width, render_overlay_png
from worker.tasks.clip_delivery import deliver_clip
from worker.tasks.environment import TaskAbandonedError, TaskEnvironment, TaskFailedError

logger = logging.getLogger(__name__)


def run_process(
    env: TaskEnvironment, lease: ActiveLease, task: ProcessTask, cancel: CancelToken
) -> None:
    raw_path = env.storage.find_raw(task.job_id)
    if raw_path is None:
        raise TaskAbandonedError("raw is not on this worker's disk")

    with env.storage.pin(task.job_id):
        try:
            clip_path = _reuse_or_render(env, task, raw_path, cancel)
            deliver_clip(env, lease, task, clip_path, cancel)
        except TaskCancelledError as cancelled:
            if cancelled.lease_lost:
                # Docs §5: on lost, discard partial output. (After a
                # successful push "lost" means "done", which deliver_clip
                # handles itself without raising.)
                env.storage.delete_clip(task.job_id)
            raise
    env.storage.delete_clip(task.job_id)


def render_inputs(task: ProcessTask) -> dict:
    """Everything that changes the rendered picture or sound."""
    fields = (
        "raw_sha256", "cut_in_s", "cut_out_s", "prova", "car_number",
        "team_name", "passagem", "show_pass", "has_audio",
    )  # fmt: skip
    return {name: getattr(task, name) for name in fields}


def _sidecar_matches(sidecar_path: Path, task: ProcessTask) -> bool:
    if not sidecar_path.exists():
        return False
    try:
        return json.loads(sidecar_path.read_text(encoding="utf-8")) == render_inputs(task)
    except ValueError:
        return False  # a damaged sidecar: render again


def _reuse_or_render(
    env: TaskEnvironment, task: ProcessTask, raw_path: Path, cancel: CancelToken
) -> Path:
    clip_path = env.storage.clip_path(task.job_id)
    sidecar_path = env.storage.clip_sidecar_path(task.job_id)
    if clip_path.exists() and _sidecar_matches(sidecar_path, task):
        logger.info("reusing the clip rendered earlier with the same inputs")
        return clip_path
    env.storage.delete_clip(task.job_id)

    render_path = env.storage.tmp_path(f"{task.job_id}.render.mp4")
    overlay_path = env.storage.tmp_path(f"{task.job_id}.overlay.png")
    started_at = time.monotonic()
    try:
        info = env.media.probe(raw_path)
        overlay = OverlayText(
            prova=task.prova,
            car_number=task.car_number,
            team_name=task.team_name,
            passagem=task.passagem,
            show_pass=task.show_pass,
        )
        render_overlay_png(overlay_path, overlay, final_frame_width(info.width, info.height))
        env.media.render_final(
            raw_path,
            render_path,
            cut_in_s=task.cut_in_s,
            cut_out_s=task.cut_out_s,
            overlay_png=overlay_path,
            info=info,
            cancel=cancel,
        )
        # Order matters for crash safety: the old sidecar is already gone,
        # so a crash between these two lines leaves a clip without a
        # sidecar, which is simply rendered again next time.
        render_path.replace(clip_path)
        sidecar_path.write_text(json.dumps(render_inputs(task)), encoding="utf-8")
    except MediaToolError as error:
        raise TaskFailedError(error.message, error.exit_code) from error
    finally:
        render_path.unlink(missing_ok=True)
        overlay_path.unlink(missing_ok=True)

    env.measurements.record(MeasurementKind.PROCESS, raw_path.stat().st_size, started_at)
    logger.info("clip rendered: %d bytes", clip_path.stat().st_size)
    return clip_path
