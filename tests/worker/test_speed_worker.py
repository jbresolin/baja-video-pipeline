"""The worker's side of LIVESTACK-67: the top speed under the clock reaches the clip.

What this module is for: for "Aceleração e Velocidade" the orchestrator
sends, next to the clock, the run's top speed in the process task
(`pass_speed`: the speed-trap mark in raw-video seconds, the speed as
written, "37,2", and its unit, "km/h"). The worker:

* says it can draw it (the render feature "speed", installer 0.8.0), so
  the orchestrator sends such tasks only to workers that can;
* draws the speed's pill (worker.overlay_speed) and gives it to the render
  command as one more picture, switched on from the speed mark;
* remembers the speed in the clip's sidecar, so a moved mark or a
  corrected speed renders the clip again;
* reports it back with the other render inputs.

What these tests prove: the four points above, with the test harness's
fake ffmpeg, which only records the commands it is given.

What they do not cover: the real render, frame by frame
(test_speed_real_ffmpeg.py), or how the pill looks (test_overlay_speed.py).
"""

import json
from pathlib import Path

from shared.models import RenderClock, RenderSpeed
from tests.worker.test_process import hold_raw, process_task
from worker.enrollment import detect_capabilities
from worker.tasks.process import render_inputs, render_inputs_report

# Car 1's first AVF run: the clock stops on 4,35 s, the speed 37,2 km/h.
CLOCK = RenderClock(largada_s=1.0, official_units=435, decimals=2, unit="s")
SPEED = RenderSpeed(speed_mark_s=6.0, text="37,2", unit="km/h")
MOVED_SPEED = RenderSpeed(speed_mark_s=6.2, text="37,2", unit="km/h")
CORRECTED_SPEED = RenderSpeed(speed_mark_s=6.0, text="36,5", unit="km/h")


def final_render_graph(harness) -> str:
    """The filter graph of the last final render the fake ffmpeg was asked for."""
    command = harness.media.command_for("final render")
    assert command, "the render did not run"
    return command[command.index("-filter_complex") + 1]


def test_every_heartbeat_and_enrollment_declare_the_speed(harness):
    # Arrange: nothing beyond a running worker.

    # Act: send one heartbeat.
    harness.heartbeater.beat_once()

    # Assert: the beat and the enrollment capabilities both name "speed".
    [sent] = harness.orchestrator.heartbeats
    assert "speed" in sent.render_features, "the heartbeat must declare the speed"
    capabilities = detect_capabilities("/no/such/ffmpeg")
    assert "speed" in capabilities.render_features, "enrollment must declare it too"


def test_a_task_with_a_speed_gives_the_render_its_pill_from_the_mark(harness, raw_bytes):
    # Arrange: a held raw, and watch the render command for the speed pill.
    job = hold_raw(harness, raw_bytes)
    seen_pills = []

    def look(what):
        # While the render runs, the speed's pill must exist on disk.
        if what == "final render":
            command = harness.media.command_for("final render")
            seen_pills.extend(arg for arg in command if arg.endswith(".speed.png"))
            assert all(Path(arg).exists() for arg in seen_pills)

    harness.media.during_render = look

    # Act: run the whole process task with a clock and a speed.
    harness.claim_and_run(process_task(job, pass_clock=CLOCK, pass_speed=SPEED))

    # Assert: the pill was one of the render's inputs...
    names = [Path(arg).name for arg in seen_pills]
    assert names == [f"{job.job_id}.speed.png"], "the speed pill must go to ffmpeg once"
    # ...switched on 4 000 000 µs into the clip (the task's cut-in is 2 s,
    # the mark 6 s raw)...
    assert "-4000000),0)'" in final_render_graph(harness), "it must appear from the mark"
    # ...and cleaned up afterwards, like the overlay PNG.
    assert not Path(seen_pills[0]).exists(), "the pill file must be deleted after the render"


def test_a_speed_without_a_clock_is_not_drawn(harness, raw_bytes):
    # Arrange: a task carrying a speed but no clock (the orchestrator never
    # sends one, but a worker must not draw a speed with nothing above it).
    job = hold_raw(harness, raw_bytes)

    # Act: run the task.
    harness.claim_and_run(process_task(job, pass_speed=SPEED))

    # Assert: only the raw and the still overlay went in, and the report
    # says there was no speed.
    command = harness.media.command_for("final render")
    assert command.count("-i") == 2, "no clock and no speed pictures must be added"
    [(_, report)] = harness.orchestrator.render_inputs
    assert report.pass_speed is None, "the report must say no speed was drawn"


def test_the_sidecar_includes_the_speed_as_json(harness, raw_bytes):
    # Arrange: a held raw and a task with a speed.
    job = hold_raw(harness, raw_bytes)
    task = process_task(job, pass_clock=CLOCK, pass_speed=SPEED)

    # Act: work out what the sidecar would record.
    inputs = render_inputs(task)

    # Assert: the speed is there as plain JSON values.
    expected = {"speed_mark_s": 6.0, "text": "37,2", "unit": "km/h"}
    assert inputs["pass_speed"] == expected, "the sidecar must remember the speed"
    assert json.loads(json.dumps(inputs)) == inputs, "the sidecar must be plain JSON"


def leave_an_old_clip(harness, job, speed: RenderSpeed) -> None:
    """A finished clip waiting in clips/ with its sidecar, as after a restart."""
    harness.storage.clip_path(job.job_id).write_bytes(b"old clip")
    old_task = process_task(job, pass_clock=CLOCK, pass_speed=speed)
    harness.storage.clip_sidecar_path(job.job_id).write_text(json.dumps(render_inputs(old_task)))


def test_a_moved_speed_mark_renders_again_instead_of_reusing_the_clip(harness, raw_bytes):
    # Arrange: a clip already rendered with the mark at 6.0 s.
    job = hold_raw(harness, raw_bytes)
    leave_an_old_clip(harness, job, SPEED)

    # Act: the same job comes back with the mark at 6.2 s.
    harness.claim_and_run(process_task(job, pass_clock=CLOCK, pass_speed=MOVED_SPEED))

    # Assert: a fresh render was delivered, not the old clip.
    stored = harness.orchestrator.stored_upload("outbox", job.job_id)
    assert stored.read_bytes().startswith(b"fake final render"), "a moved mark must re-render"


def test_a_corrected_speed_renders_again_instead_of_reusing_the_clip(harness, raw_bytes):
    # Arrange: a clip already rendered with 37,2 km/h.
    job = hold_raw(harness, raw_bytes)
    leave_an_old_clip(harness, job, SPEED)

    # Act: the judges corrected it to 36,5 km/h.
    harness.claim_and_run(process_task(job, pass_clock=CLOCK, pass_speed=CORRECTED_SPEED))

    # Assert: a fresh render was delivered.
    stored = harness.orchestrator.stored_upload("outbox", job.job_id)
    assert stored.read_bytes().startswith(b"fake final render"), "a new speed must re-render"


def test_the_same_speed_reuses_the_clip(harness, raw_bytes):
    # Arrange: a clip already rendered with exactly this clock and speed.
    job = hold_raw(harness, raw_bytes)
    leave_an_old_clip(harness, job, SPEED)

    # Act: the same task again (e.g. after a restart).
    harness.claim_and_run(process_task(job, pass_clock=CLOCK, pass_speed=SPEED))

    # Assert: nothing was rendered, the old clip went out.
    renders = [what for what, _ in harness.media.commands if what == "final render"]
    assert renders == [], "an unchanged speed must not render again"
    stored = harness.orchestrator.stored_upload("outbox", job.job_id)
    assert stored.read_bytes() == b"old clip", "the clip already made must be delivered"


def test_the_report_carries_the_speed_as_drawn(harness, raw_bytes):
    # Arrange: a held raw.
    job = hold_raw(harness, raw_bytes)

    # Act: render with a clock and a speed.
    harness.claim_and_run(process_task(job, pass_clock=CLOCK, pass_speed=SPEED))

    # Assert: the report names the same speed, and a clip without one says so.
    [(_, report)] = harness.orchestrator.render_inputs
    assert report.pass_speed is not None, "the report must carry the speed"
    assert report.pass_speed.model_dump() == SPEED.model_dump(), "exactly as the task gave it"
    without = render_inputs_report(process_task(job, pass_clock=CLOCK), "0" * 64)
    assert without.pass_speed is None, "a clip without a speed must report none"


def test_a_clip_left_by_a_worker_before_0_8_0_is_rendered_again(harness, raw_bytes):
    # Arrange: a clip with a clock left by 0.7.x, whose sidecar has no
    # pass_speed key at all (it did not know the speed).
    job = hold_raw(harness, raw_bytes)
    harness.storage.clip_path(job.job_id).write_bytes(b"old clip")
    old_inputs = render_inputs(process_task(job, pass_clock=CLOCK))
    del old_inputs["pass_speed"]
    harness.storage.clip_sidecar_path(job.job_id).write_text(json.dumps(old_inputs))

    # Act: after the update, the same job comes with a speed.
    harness.claim_and_run(process_task(job, pass_clock=CLOCK, pass_speed=SPEED))

    # Assert: rendered again (with the speed), not the old clip reused.
    stored = harness.orchestrator.stored_upload("outbox", job.job_id)
    assert stored.read_bytes().startswith(b"fake final render"), "an old sidecar must re-render"
