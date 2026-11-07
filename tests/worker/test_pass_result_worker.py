"""The worker's side of LIVESTACK-47: the official pass result reaches the clip.

Background for a beginner: the orchestrator reads the official results
of a passagem (time, cones, ...) and sends them in the process task as one
ready-made line, `pass_result_text` (e.g. "66 s · 4 cones → 86 s"). The
worker never fetches results itself; it only:

* says it can draw that line (the render feature "pass_result"), at
  enrollment and in every heartbeat, so the orchestrator sends such tasks
  only to workers of version 0.5.0 or newer;
* draws it as a third pill on the overlay (tests/worker/test_overlay_result.py
  checks the picture itself);
* remembers it in the clip's "sidecar" (the note of what a clip was
  rendered from), so a corrected result renders the clip again;
* reports it back to the orchestrator with the other render inputs.
"""

import json

from PIL import Image

from tests.worker.test_overlay import pill_rows
from tests.worker.test_process import hold_raw, process_task
from worker.enrollment import detect_capabilities
from worker.render_features import DECLARED_RENDER_FEATURES
from worker.tasks.process import overlay_text_for, render_inputs, render_inputs_report

RESULT = "66 s · 4 cones → 86 s"
CORRECTED_RESULT = "66 s · 5 cones → 91 s"


# --- Declaring that this worker can draw the result ---


def test_every_heartbeat_declares_pass_result(harness):
    # What it does: one heartbeat, as the worker sends every 15 s.
    harness.heartbeater.beat_once()

    # The beat the fake orchestrator received lists "pass_result", which is
    # what lets the orchestrator hand this worker a task with a result line.
    [sent] = harness.orchestrator.heartbeats
    assert "pass_result" in sent.render_features


def test_enrollment_declares_pass_result_too():
    # No ffmpeg at that path: the capability probe still works (it falls
    # back to the software encoder), and the features do not depend on it.
    capabilities = detect_capabilities("/no/such/ffmpeg")
    assert "pass_result" in capabilities.render_features
    assert capabilities.render_features == DECLARED_RENDER_FEATURES


# --- From the task to the overlay ---


def test_the_task_text_goes_to_the_overlay_unchanged(harness, raw_bytes):
    # Setup: a held raw and a process task carrying a result line.
    job = hold_raw(harness, raw_bytes)
    task = process_task(job, pass_result_text=RESULT)

    # overlay_text_for is what the render uses to build the overlay: the
    # result arrives exactly as the orchestrator wrote it (the overlay
    # itself puts it in capitals when drawing).
    assert overlay_text_for(task).pass_result == RESULT
    # A task without a result gives an overlay without one.
    assert overlay_text_for(process_task(job)).pass_result is None


def overlay_rows_during_render(harness) -> list[list[tuple[int, int]]]:
    """The pills (as opaque row ranges at x=100) of the overlay PNG while the render runs.

    The worker deletes the PNG after rendering, so we look at it from the
    fake ffmpeg's "during render" hook, like test_render_options_worker.py.
    """
    seen = []

    def look(what):
        if what != "final render":
            return
        command = harness.media.command_for("final render")
        overlay = next(arg for arg in command if arg.endswith(".overlay.png"))
        with Image.open(overlay) as image:
            seen.append(pill_rows(image, 100))

    harness.media.during_render = look
    return seen


def test_a_task_with_a_result_renders_a_third_pill(harness, raw_bytes):
    # Setup: a held raw; we watch the overlay PNG the render is given.
    job = hold_raw(harness, raw_bytes)
    seen = overlay_rows_during_render(harness)

    # What it does: run the whole process task with a result line.
    harness.claim_and_run(process_task(job, pass_result_text=RESULT))

    # Three pills in the column x=100: name, role (ending at y 940 as
    # always), and the 40 px result pill 3 px below it. Two would mean the
    # result never reached the overlay.
    assert seen == [[(833, 896), (900, 939), (943, 982)]]


def test_a_task_without_a_result_renders_the_two_pills_as_before(harness, raw_bytes):
    job = hold_raw(harness, raw_bytes)
    seen = overlay_rows_during_render(harness)

    harness.claim_and_run(process_task(job))

    # Only the name and role pills, exactly where they were before 0.5.0.
    assert seen == [[(833, 896), (900, 939)]]


# --- The sidecar: a changed result renders the clip again ---


def test_the_sidecar_includes_the_result_text(harness, raw_bytes):
    # render_inputs() is what is written into the sidecar next to a clip.
    job = hold_raw(harness, raw_bytes)
    inputs = render_inputs(process_task(job, pass_result_text=RESULT))
    assert inputs["pass_result_text"] == RESULT


def leave_an_old_clip(harness, job, result_text: str | None) -> None:
    """Put a finished clip and its sidecar in clips/, as a render that was not delivered yet."""
    harness.storage.clip_path(job.job_id).write_bytes(b"old clip")
    old_task = process_task(job, pass_result_text=result_text)
    harness.storage.clip_sidecar_path(job.job_id).write_text(json.dumps(render_inputs(old_task)))


def test_a_corrected_result_renders_again_instead_of_reusing_the_clip(harness, raw_bytes):
    # Setup: a clip rendered with the first result is waiting in clips/
    # (e.g. the worker restarted before delivering it).
    job = hold_raw(harness, raw_bytes)
    leave_an_old_clip(harness, job, RESULT)

    # What it does: the same job is offered again, but the judges have
    # corrected the result in the meantime.
    harness.claim_and_run(process_task(job, pass_result_text=CORRECTED_RESULT))

    # The render ran, and the outbox got the new clip, not the old bytes
    # (which show the outdated result).
    assert harness.media.command_for("final render")
    stored = harness.orchestrator.stored_upload("outbox", job.job_id)
    assert stored.read_bytes().startswith(b"fake final render")


def test_the_same_result_reuses_the_clip(harness, raw_bytes):
    # The opposite case, to show the test above is not "always renders":
    # with exactly the same inputs the waiting clip is delivered as is.
    job = hold_raw(harness, raw_bytes)
    leave_an_old_clip(harness, job, RESULT)

    harness.claim_and_run(process_task(job, pass_result_text=RESULT))

    assert [what for what, _ in harness.media.commands if what == "final render"] == []
    stored = harness.orchestrator.stored_upload("outbox", job.job_id)
    assert stored.read_bytes() == b"old clip"


# --- The render-inputs report ---


def test_the_report_carries_the_result_as_drawn(harness, raw_bytes):
    # Setup and action: render and deliver a clip with a result line.
    job = hold_raw(harness, raw_bytes)
    harness.claim_and_run(process_task(job, pass_result_text=RESULT))

    # The orchestrator received one render-inputs report for this job, and
    # it says which result line the clip shows.
    [(job_id, report)] = harness.orchestrator.render_inputs
    assert job_id == job.job_id
    assert report.pass_result_text == RESULT


def test_a_blank_result_is_reported_as_none(harness, raw_bytes):
    # A line of spaces draws no pill (worker.overlay), so the report says
    # "no result line" rather than the spaces: it describes the picture.
    job = hold_raw(harness, raw_bytes)
    task = process_task(job, pass_result_text="   ")
    assert render_inputs_report(task, "0" * 64).pass_result_text is None
    assert render_inputs_report(process_task(job), "0" * 64).pass_result_text is None
