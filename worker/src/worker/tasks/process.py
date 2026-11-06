"""process: render the final clip from a held raw, then deliver it.

Render (docs/api.md §5, contract §30): trim to the reviewer's cuts, draw
the overlay PNG bottom-left, loudnorm, tone-map HDR. Delivery is in
worker.tasks.clip_delivery (direct push to the studio, else the outbox).

Before delivering, the worker tells the orchestrator what the clip was
rendered from (PUT /jobs/{id}/render-inputs, LIVESTACK-43), so the review
UI can say when a clip on air no longer matches the review.

The finished clip waits in clips/<job_id>.mp4 next to a small JSON
"sidecar" naming the inputs it was rendered from. If the worker crashes
after rendering and is offered the same job again with the same inputs,
it skips the render and resumes the upload. A lost lease (the reviewer
changed the cut, or the lease expired) deletes the clip.

After a verified delivery the clip is *kept* (design §11 as amended:
processed clips are the archive) as clips/<clip name>.<job_id>.<sha256>.mp4
(e.g. enduro-042-p3.<job>.<sha>.mp4), until
the master has archived it (worker.clip_store).
"""

import json
import logging
import time
from pathlib import Path, PurePosixPath

from shared.hashing import sha256_of_file
from shared.models import MeasurementKind, ProcessTask, RenderInputsReport
from worker import overlay_tokens
from worker.api_client import ApiError, OrchestratorUnreachableError
from worker.failures import TaskFailedError, probe_input
from worker.leases import ActiveLease, CancelToken, TaskCancelledError
from worker.overlay import OverlayText, final_frame_width, render_overlay_png
from worker.tasks.clip_delivery import deliver_clip
from worker.tasks.environment import TaskEnvironment

logger = logging.getLogger(__name__)


def run_process(
    env: TaskEnvironment, lease: ActiveLease, task: ProcessTask, cancel: CancelToken
) -> None:
    raw_path = env.storage.find_raw(task.job_id)
    if raw_path is None:
        # Our problem, not the job's: failure scope "worker" (worker.failures).
        raise TaskFailedError("the raw is not on this worker's disk")

    with env.storage.pin(task.job_id):
        try:
            clip_path = _reuse_or_render(env, task, raw_path, cancel)
            clip_sha256 = sha256_of_file(clip_path)
            report_render_inputs(env, lease, task, clip_sha256)
            deliver_clip(env, lease, task, clip_path, cancel, clip_sha256=clip_sha256)
        except TaskCancelledError as cancelled:
            if cancelled.lease_lost:
                # Docs §5: on lost, discard partial output. (After a
                # successful push "lost" means "done", which deliver_clip
                # handles itself without raising.)
                env.storage.delete_clip(task.job_id)
            raise
    clip_name = PurePosixPath(task.clip_relative_path).stem  # "enduro-042-p3" (item 12)
    env.storage.clips.keep(clip_path, task.job_id, clip_sha256, clip_name)
    env.storage.delete_clip(task.job_id)  # only the sidecar is left by now


def render_inputs(task: ProcessTask) -> dict:
    """Everything that changes the rendered picture or sound."""
    fields = (
        "raw_sha256", "cut_in_s", "cut_out_s", "prova", "car_number",
        "team_name", "school_short", "passagem", "show_pass", "has_audio",
    )  # fmt: skip
    return {name: getattr(task, name) for name in fields}


def render_inputs_report(task: ProcessTask, clip_sha256: str) -> RenderInputsReport:
    """What the clip was rendered from, for the orchestrator (LIVESTACK-43).

    The task's values are what the render used (a reused clip matched them
    exactly, see _sidecar_matches), plus this worker's overlay theme.
    """
    overlay = overlay_text_for(task)
    return RenderInputsReport(
        clip_sha256=clip_sha256,
        raw_sha256=task.raw_sha256,
        cut_in_s=task.cut_in_s,
        cut_out_s=task.cut_out_s,
        prova=task.prova,
        car_number=task.car_number,
        passagem=task.passagem,
        show_pass=task.show_pass,
        overlay_team_name=overlay.team_name,
        overlay_school_name=overlay.school_short,
        overlay_theme_version=overlay_tokens.THEME_VERSION,
    )


def report_render_inputs(
    env: TaskEnvironment, lease: ActiveLease, task: ProcessTask, clip_sha256: str
) -> None:
    """Send the report before delivering; a refusal never stops the delivery.

    Without it the orchestrator keeps its own record of the task it handed
    out (an orchestrator older than contract 2.4.0 answers 404: fine too).
    """
    try:
        env.api.report_render_inputs(
            task.job_id, lease.lease_id, render_inputs_report(task, clip_sha256)
        )
    except (ApiError, OrchestratorUnreachableError) as error:
        logger.warning("render inputs not reported (%s); delivering anyway", error)


def overlay_text_for(task: ProcessTask) -> OverlayText:
    """What the clip overlay shows, straight from the orchestrator's task."""
    return OverlayText(
        prova=task.prova,
        car_number=task.car_number,
        team_name=task.team_name,
        passagem=task.passagem,
        show_pass=task.show_pass,
        school_short=task.school_short,
    )


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
        # Unreadable raw → scope "job"; a failing ffmpeg render
        # (MediaToolError) → scope "worker" (worker.failures).
        info = probe_input(env.media, raw_path)
        overlay = overlay_text_for(task)
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
    finally:
        render_path.unlink(missing_ok=True)
        overlay_path.unlink(missing_ok=True)

    env.measurements.record(MeasurementKind.PROCESS, raw_path.stat().st_size, started_at)
    logger.info("clip rendered: %d bytes", clip_path.stat().st_size)
    return clip_path
