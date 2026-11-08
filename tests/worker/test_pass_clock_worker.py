"""The worker's side of LIVESTACK-48: the running clock reaches the clip.

Background for a beginner: the orchestrator sends the clock in the
process task (`pass_clock`: the largada in raw-video seconds, the official
time in clock units, the decimals). The worker:

* says it can draw it (the render feature "clock"), so the orchestrator
  sends such tasks only to workers of version 0.6.0 or newer;
* draws the clock's pill (worker.overlay_clock) and gives it to the render
  command, whose drawtext writes the digits (shared.ffmpeg_clock);
* remembers the clock in the clip's sidecar, so a changed largada or
  official time renders the clip again;
* reports it back with the other render inputs.

The fake ffmpeg of the test harness only records commands; the real
render is checked frame by frame in tests/worker/test_clock_real_ffmpeg.py.
"""

import json
from pathlib import Path

from shared.models import RenderClock
from tests.worker.test_process import hold_raw, process_task
from worker.enrollment import detect_capabilities
from worker.tasks.process import render_inputs, render_inputs_report

CLOCK = RenderClock(largada_s=2.5, official_units=660, decimals=1)
MOVED_CLOCK = RenderClock(largada_s=2.6, official_units=660, decimals=1)


def test_every_heartbeat_and_enrollment_declare_the_clock(harness):
    harness.heartbeater.beat_once()
    [sent] = harness.orchestrator.heartbeats
    assert "clock" in sent.render_features
    assert "clock" in detect_capabilities("/no/such/ffmpeg").render_features


def final_render_command(harness) -> list[str]:
    command = harness.media.command_for("final render")
    assert command, "the render did not run"
    return command


def test_a_task_with_a_clock_gives_the_render_its_pill(harness, raw_bytes):
    # Setup: a held raw, and a task with a clock (largada 2.5 s, 66,0).
    job = hold_raw(harness, raw_bytes)
    seen_pills = []

    def look(what):
        # While the render runs, the clock's pill exists next to the overlay.
        if what == "final render":
            command = harness.media.command_for("final render")
            seen_pills.extend(arg for arg in command if arg.endswith(".clock.png"))
            assert all(Path(arg).exists() for arg in seen_pills)

    harness.media.during_render = look

    # What it does: run the whole process task.
    harness.claim_and_run(process_task(job, pass_clock=CLOCK))

    # The command got the pill as its third input...
    assert [Path(arg).name for arg in seen_pills] == [f"{job.job_id}.clock.png"]
    # ...and the filter graph writes the digits from the largada on.
    command = final_render_command(harness)
    graph = command[command.index("-filter_complex") + 1]
    assert "drawtext=" in graph
    assert "enable='gte(" in graph
    # The pill is cleaned up afterwards, like the overlay PNG.
    assert not Path(seen_pills[0]).exists()


def test_a_task_without_a_clock_renders_exactly_as_before(harness, raw_bytes):
    job = hold_raw(harness, raw_bytes)
    harness.claim_and_run(process_task(job))
    command = final_render_command(harness)
    # Only the raw and the overlay PNG as inputs, no clock steps.
    assert command.count("-i") == 2
    assert "clock" not in command[command.index("-filter_complex") + 1]


def test_the_sidecar_includes_the_clock_as_json(harness, raw_bytes):
    job = hold_raw(harness, raw_bytes)
    inputs = render_inputs(process_task(job, pass_clock=CLOCK))
    # Plain JSON values, so the sidecar file can be written and compared.
    assert inputs["pass_clock"] == {"largada_s": 2.5, "official_units": 660, "decimals": 1}
    assert json.loads(json.dumps(inputs)) == inputs


def leave_an_old_clip(harness, job, clock: RenderClock | None) -> None:
    """A finished clip waiting in clips/ with its sidecar, as after a restart."""
    harness.storage.clip_path(job.job_id).write_bytes(b"old clip")
    old_task = process_task(job, pass_clock=clock)
    harness.storage.clip_sidecar_path(job.job_id).write_text(json.dumps(render_inputs(old_task)))


def test_a_moved_largada_renders_again_instead_of_reusing_the_clip(harness, raw_bytes):
    job = hold_raw(harness, raw_bytes)
    leave_an_old_clip(harness, job, CLOCK)

    harness.claim_and_run(process_task(job, pass_clock=MOVED_CLOCK))

    stored = harness.orchestrator.stored_upload("outbox", job.job_id)
    assert stored.read_bytes().startswith(b"fake final render")


def test_the_same_clock_reuses_the_clip(harness, raw_bytes):
    job = hold_raw(harness, raw_bytes)
    leave_an_old_clip(harness, job, CLOCK)

    harness.claim_and_run(process_task(job, pass_clock=CLOCK))

    assert [what for what, _ in harness.media.commands if what == "final render"] == []
    stored = harness.orchestrator.stored_upload("outbox", job.job_id)
    assert stored.read_bytes() == b"old clip"


def test_the_report_carries_the_clock_as_drawn(harness, raw_bytes):
    job = hold_raw(harness, raw_bytes)
    harness.claim_and_run(process_task(job, pass_clock=CLOCK))

    [(_, report)] = harness.orchestrator.render_inputs
    assert report.pass_clock is not None
    assert report.pass_clock.model_dump() == CLOCK.model_dump()
    # And a clip without a clock says so.
    assert render_inputs_report(process_task(job), "0" * 64).pass_clock is None
