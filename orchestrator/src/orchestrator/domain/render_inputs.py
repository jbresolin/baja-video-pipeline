"""What each clip version was rendered from, and whether it still matches (LIVESTACK-43).

João changed the cut of a clip that was already on air; the save worked,
but nothing said that the clip on air no longer matched the form. Now:

1. When a worker claims a process task, the orchestrator keeps a snapshot
   of what it handed out, with the Settings' name choices
   (`claimed_render_inputs_json`). The worker reports what it actually
   rendered from before delivering (render_inputs_report.py,
   `reported_render_inputs_json`). When the clip becomes a version
   (clip_archive.record_clip_version), the version keeps the worker's
   report for that clip, else the snapshot.
2. compare_newest_clip() puts the newest version's inputs next to what a
   render would use now (the review as saved, the name choices as they
   are) and lists every input that differs. The job page, the clips page
   and the queue filter "Clipes desatualizados" show it.

Only inputs the orchestrator can know "now" are compared: the overlay
theme version is kept for the record but not compared, because only
workers know which theme they have.

The official pass result (LIVESTACK-47) is compared as the text the
overlay would show with the results fetched last: a result published or
corrected by the judges after the clip was rendered makes it out of date,
exactly like an edited cut. Reprocessing stays the reviewer's decision.
The running clock (LIVESTACK-48) likewise: its largada, its official time
(from the latest fetch) and its decimals.
"""

import sqlite3
from datetime import datetime

from orchestrator.domain.clip_names import NameStyles, clip_names_for_job, name_styles
from orchestrator.domain.pass_clock import clock_for_render
from orchestrator.domain.pass_results import pass_text_for_render
from orchestrator.repositories import clips as clips_repo
from orchestrator.repositories import jobs as jobs_repo
from shared.framing import effective_framing
from shared.models import (
    ClipInputsComparison,
    Framing,
    JobState,
    ProcessTask,
    RenderClock,
    RenderInputField,
    RenderInputs,
    RenderInputsReport,
)

# Cuts are floats typed in a form; anything closer than half a millisecond
# is the same cut (a frame is 33 ms).
SAME_CUT_TOLERANCE_S = 0.0005
# The crop band's place comes from a slider; half a percent is the same place.
SAME_CROP_TOLERANCE = 0.005


def picture_inputs(
    job_row: sqlite3.Row,
    *,
    rotation: int | None,
    framing: Framing | None,
    crop_position: float | None,
    no_audio: bool | None,
) -> dict:
    """The render options (LIVESTACK-50) as RenderInputs fields.

    Framing and the crop band are kept only when they show, i.e. when the
    picture is not 16:9 after the turn (shared.framing.effective_framing);
    the picture size is the job's (from its probe report). None for
    `framing` means the worker used none: a worker before 0.4.0, which
    reports no options and renders with no turn and the sound kept.
    """
    shown_framing, shown_position = (None, None)
    if framing is not None:
        shown_framing, shown_position = effective_framing(
            job_row["display_width"],
            job_row["display_height"],
            rotation=rotation or 0,
            framing=framing,
            crop_position=0.5 if crop_position is None else crop_position,
        )
    return {
        "rotation": rotation or 0,
        "framing": shown_framing,
        "crop_position": shown_position,
        "no_audio": bool(no_audio),
    }


def inputs_from_task(task: ProcessTask, styles: NameStyles, job_row: sqlite3.Row) -> RenderInputs:
    """The orchestrator's snapshot of a process task it hands out."""
    return RenderInputs(
        raw_sha256=task.raw_sha256,
        cut_in_s=task.cut_in_s,
        cut_out_s=task.cut_out_s,
        prova=task.prova,
        car_number=task.car_number,
        passagem=task.passagem,
        show_pass=task.show_pass,
        team_name_style=styles.team,
        school_name_style=styles.school,
        overlay_team_name=task.team_name,
        # ProcessTask.school_short is the sigla or the full school name,
        # whichever the Settings chose (clip_names.py).
        overlay_school_name=task.school_short,
        **picture_inputs(
            job_row,
            rotation=task.rotation,
            framing=task.framing,
            crop_position=task.crop_position,
            no_audio=task.no_audio,
        ),
        pass_result_text=task.pass_result_text,
        pass_clock=task.pass_clock,
        reported_by_worker=False,
    )


def remember_claimed_inputs(
    connection: sqlite3.Connection, job_id: str, task: ProcessTask, now: datetime
) -> None:
    """At a process claim: snapshot the task with the Settings' name choices.

    Stored in jobs.claimed_render_inputs_json; it becomes the clip
    version's inputs if the worker reports none. Machine activity: no
    review version bump.
    """
    job_row = jobs_repo.get_job_row(connection, job_id)
    snapshot = inputs_from_task(task, name_styles(connection), job_row).model_dump_json()
    jobs_repo.update_job(connection, job_id, now, {"claimed_render_inputs_json": snapshot})


def inputs_from_report(
    report: RenderInputsReport, claimed: RenderInputs | None, job_row: sqlite3.Row
) -> RenderInputs:
    """A worker's report as the version's inputs.

    The worker's own values win; the name choices come from the claim
    snapshot, because the task carries only the names already chosen.
    """
    return RenderInputs(
        raw_sha256=report.raw_sha256,
        cut_in_s=report.cut_in_s,
        cut_out_s=report.cut_out_s,
        prova=report.prova,
        car_number=report.car_number,
        passagem=report.passagem,
        show_pass=report.show_pass,
        team_name_style=claimed.team_name_style if claimed else None,
        school_name_style=claimed.school_name_style if claimed else None,
        overlay_team_name=report.overlay_team_name,
        overlay_school_name=report.overlay_school_name,
        overlay_theme_version=report.overlay_theme_version,
        **picture_inputs(
            job_row,
            rotation=report.rotation,
            framing=report.framing,
            crop_position=report.crop_position,
            no_audio=report.no_audio,
        ),
        pass_result_text=report.pass_result_text,
        pass_clock=(
            None if report.pass_clock is None else RenderClock(**report.pass_clock.model_dump())
        ),
        reported_by_worker=True,
    )


def _load_inputs(text: str | None) -> RenderInputs | None:
    return None if text is None else RenderInputs.model_validate_json(text)


def inputs_json_for_clip(job_row: sqlite3.Row, clip_sha256: str) -> str | None:
    """What to store as a new clip version's inputs (None: nothing is known).

    The rendering worker's report when it is about this very clip, else
    the snapshot of the current process claim.
    """
    claimed = _load_inputs(job_row["claimed_render_inputs_json"])
    reported_text = job_row["reported_render_inputs_json"]
    if reported_text is not None:
        report = RenderInputsReport.model_validate_json(reported_text)
        if report.clip_sha256 == clip_sha256:
            return inputs_from_report(report, claimed, job_row).model_dump_json()
    return claimed.model_dump_json() if claimed is not None else None


def inputs_now(
    connection: sqlite3.Connection, styles: NameStyles, job_row: sqlite3.Row
) -> RenderInputs:
    """What a render would use if it started now (the same names and result line
    a process task gets)."""
    names = clip_names_for_job(styles, job_row)
    return RenderInputs(
        raw_sha256=job_row["sha256"],
        cut_in_s=job_row["cut_in_s"],
        cut_out_s=job_row["cut_out_s"],
        prova=job_row["prova"],
        car_number=job_row["car_number"],
        passagem=job_row["passagem"],
        show_pass=bool(job_row["show_pass"]),
        team_name_style=styles.team,
        school_name_style=styles.school,
        overlay_team_name=names.team,
        overlay_school_name=names.school,
        **picture_inputs(
            job_row,
            rotation=job_row["rotation"],
            framing=Framing(job_row["framing"]),
            crop_position=job_row["crop_position"],
            no_audio=bool(job_row["no_audio"]),
        ),
        pass_result_text=pass_text_for_render(connection, job_row),
        pass_clock=clock_for_render(connection, job_row),
    )


def _same_cut(first: float | None, second: float | None) -> bool:
    if first is None or second is None:
        return first is second
    return abs(first - second) <= SAME_CUT_TOLERANCE_S


def changed_inputs(clip: RenderInputs, now: RenderInputs) -> list[RenderInputField]:
    """Every input that would make a new render look different, in a fixed order.

    The name choices themselves are not compared, only the names they give:
    switching "short" to "full" for a team whose two names are equal
    changes nothing on screen, so the clip is not out of date.
    """
    changed = []
    same_cut = _same_cut(clip.cut_in_s, now.cut_in_s) and _same_cut(clip.cut_out_s, now.cut_out_s)
    if not same_cut:
        changed.append(RenderInputField.CUT)
    plain_fields = [
        (RenderInputField.PROVA, clip.prova, now.prova),
        (RenderInputField.CAR_NUMBER, clip.car_number, now.car_number),
        (RenderInputField.PASSAGEM, clip.passagem, now.passagem),
        (RenderInputField.SHOW_PASS, clip.show_pass, now.show_pass),
        (RenderInputField.TEAM_NAME, clip.overlay_team_name, now.overlay_team_name),
        (RenderInputField.SCHOOL_NAME, clip.overlay_school_name, now.overlay_school_name),
        (RenderInputField.RAW, clip.raw_sha256, now.raw_sha256),
    ]
    for field, clip_value, now_value in plain_fields:
        if clip_value != now_value:
            changed.append(field)
    # Render options (LIVESTACK-50). Inputs recorded before them have
    # rotation and no_audio None: what was rendered then, 0 and False.
    if (clip.rotation or 0) != (now.rotation or 0):
        changed.append(RenderInputField.ROTATION)
    if not _same_framing(clip, now):
        changed.append(RenderInputField.FRAMING)
    if bool(clip.no_audio) != bool(now.no_audio):
        changed.append(RenderInputField.AUDIO)
    # The result line (LIVESTACK-47); None on both sides = no pill either way.
    if clip.pass_result_text != now.pass_result_text:
        changed.append(RenderInputField.PASS_RESULT)
    # The running clock (LIVESTACK-48); None on both sides = no clock either way.
    if not _same_clock(clip.pass_clock, now.pass_clock):
        changed.append(RenderInputField.CLOCK)
    return changed


def _same_clock(clip: RenderClock | None, now: RenderClock | None) -> bool:
    """Same official time and decimals, and the largada at (nearly) the same moment."""
    if clip is None or now is None:
        return clip is now
    return (
        clip.official_units == now.official_units
        and clip.decimals == now.decimals
        and _same_cut(clip.largada_s, now.largada_s)
    )


def _same_framing(clip: RenderInputs, now: RenderInputs) -> bool:
    """Same framing as it shows, and for "crop" the band in (nearly) the same place."""
    if clip.framing != now.framing:
        return False
    if clip.framing != Framing.CROP:
        return True
    return abs((clip.crop_position or 0.5) - (now.crop_position or 0.5)) <= SAME_CROP_TOLERANCE


def compare_newest_clip(
    connection: sqlite3.Connection, job_row: sqlite3.Row, styles: NameStyles | None = None
) -> ClipInputsComparison | None:
    """The newest clip version next to the review now; None when there is nothing to compare.

    Nothing to compare: no clip version yet, a version rendered before
    inputs were recorded, or a discarded job (no clip of it will be made).
    `styles` may be passed by callers that compare many jobs at once.
    """
    if job_row["state"] == JobState.DISCARDED:
        return None
    versions = clips_repo.list_versions(connection, job_row["id"])
    if not versions:
        return None
    newest = versions[-1]
    clip = _load_inputs(newest["render_inputs_json"])
    if clip is None:
        return None
    now = inputs_now(connection, styles or name_styles(connection), job_row)
    return ClipInputsComparison(
        version=newest["version"], clip=clip, now=now, changed=changed_inputs(clip, now)
    )
