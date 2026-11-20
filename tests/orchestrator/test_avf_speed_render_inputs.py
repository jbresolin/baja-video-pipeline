"""The speed as a render input: a delivered clip no longer matches when it changes (LIVESTACK-67).

What this module is for: every clip version remembers what it was rendered
from (LIVESTACK-43), and the orchestrator compares that with what a render
would use now. For "Aceleração e Velocidade" the top speed under the clock
is one more input: its mark and the official speed as written ("37,2")
with its unit. So a delivered clip shows as out of date ("speed" in
`clip_inputs.changed`) when the judges publish the speed after the clip
was made, when they correct it, or when the reviewer moves the mark.
Reprocessing stays the reviewer's decision.

What these tests prove: the worker's report of the speed becomes the
version's input; publishing, correcting, and moving the mark each flag the
clip with exactly "speed"; a correction that rounds to the same text, and
a moment with no change, flag nothing.

What they do not cover: the sentence the job page writes for it (the
review UI's test_avf_speed_outdated_view.py).
"""

import hashlib

from orchestrator.domain.pass_results_fetch import poll_due_tables
from orchestrator.testing.scenarios import SAMPLE_CLIP, claim_ok
from shared.models import TaskKind, WorkerRole
from tests.orchestrator.avf_data import SPEED_TABLE, avf_job_in_review, set_run_value, set_up_avf
from tests.orchestrator.test_pass_results_render import enroll_master
from tests.orchestrator.test_render_inputs import edit_review, report_inputs

FEATURES_0_8_0 = ["framing", "no_audio", "pass_result", "clock", "clock_top_right", "speed"]
LARGADA_S = 1.0
SPEED_MARK_S = 4.0


def deliver(harness, master, studio, job_id) -> dict:
    """The master claims, reports what it drew (as 0.8.0 does), and the studio ACKs."""
    lease_id, task = claim_ok(harness, master, job_id, TaskKind.PROCESS)
    sha256 = hashlib.sha256(SAMPLE_CLIP).hexdigest()
    reported = report_inputs(
        harness, master, job_id, lease_id, task, sha256,
        pass_clock=task["pass_clock"], pass_speed=task["pass_speed"],
    )  # fmt: skip
    assert reported.status_code == 204, reported.text
    ack = harness.client.post(
        f"/jobs/{job_id}/delivered",
        json={
            "sha256": sha256,
            "size_bytes": len(SAMPLE_CLIP),
            "relative_path": task["clip_relative_path"],
        },
        headers=studio.headers,
    )
    assert ack.status_code == 200, ack.text
    return task


def delivered_avf_clip(harness) -> str:
    """Car 1's first AVF run, both marks set, rendered by a 0.8.0 master and delivered."""
    master = enroll_master(harness, FEATURES_0_8_0)
    studio = harness.enroll(WorkerRole.STUDIO, "studio", tailnet_address="100.70.0.3")
    job_id = avf_job_in_review(harness, car=1, passagem=1, worker=master)
    poll_due_tables(harness.context)
    edit_review(
        harness,
        job_id,
        action="ready",
        cut_in_s=0.5,
        cut_out_s=5.0,
        largada_s=LARGADA_S,
        speed_mark_s=SPEED_MARK_S,
    )
    deliver(harness, master, studio, job_id)
    return job_id


def judges_change_the_speed(harness, value) -> None:
    """Car 1's first run in the speed table becomes `value`; the next fetch (30 s later) sees it."""
    set_run_value(harness.results_api.tables[SPEED_TABLE], car_number=1, run=1, value=value)
    harness.clock.advance(30)
    poll_due_tables(harness.context)


def changed_inputs(harness, job_id) -> list[str]:
    """What the job page lists as different between the clip on air and the review now."""
    return harness.job(job_id)["clip_inputs"]["changed"]


def test_the_workers_report_of_the_speed_becomes_the_versions_input(harness):
    # Arrange: AVF configured with both tables served.
    set_up_avf(harness)

    # Act: render and deliver car 1's first run with both marks.
    job_id = delivered_avf_clip(harness)

    # Assert: the version remembers the speed as drawn, and nothing differs yet.
    inputs = harness.job(job_id)["clip_versions"][0]["render_inputs"]
    expected = {"speed_mark_s": SPEED_MARK_S, "text": "37,2", "unit": "km/h"}
    assert inputs["pass_speed"] == expected, "the clip's speed must be recorded"
    assert changed_inputs(harness, job_id) == [], "a fresh clip matches the review"


def test_a_corrected_speed_flags_the_delivered_clip(harness):
    # Arrange: car 1's clip delivered with 37,2 km/h.
    set_up_avf(harness)
    job_id = delivered_avf_clip(harness)

    # Act: the judges correct the speed to 36.0 km/h.
    judges_change_the_speed(harness, 36.0)

    # Assert: the clip on air no longer matches, because of the speed only.
    assert changed_inputs(harness, job_id) == ["speed"], "a corrected speed must flag the clip"
    now = harness.job(job_id)["clip_inputs"]["now"]["pass_speed"]
    assert now["text"] == "36,0", "what a render would draw now"


def test_a_correction_that_rounds_to_the_same_text_changes_nothing(harness):
    # Arrange: car 1's clip delivered with 37,2 km/h (37.18).
    set_up_avf(harness)
    job_id = delivered_avf_clip(harness)

    # Act: the judges write 37.21, which is still "37,2" with 1 decimal.
    judges_change_the_speed(harness, 37.21)

    # Assert: the pill would look the same, so the clip is not out of date.
    assert changed_inputs(harness, job_id) == [], "the same text on screen is no change"


def test_a_speed_published_after_the_clip_was_made_flags_it(harness):
    # Arrange: the speed of car 1's first run is not published yet ("-"),
    # so the clip goes out with the clock and no speed.
    set_up_avf(harness)
    set_run_value(harness.results_api.tables[SPEED_TABLE], car_number=1, run=1, value="-")
    job_id = delivered_avf_clip(harness)
    clip = harness.job(job_id)["clip_versions"][0]["render_inputs"]
    assert clip["pass_speed"] is None, "the clip was made without a speed"

    # Act: the judges publish 37.18 km/h.
    judges_change_the_speed(harness, 37.18)

    # Assert: the clip is flagged, and a reprocess would draw the speed.
    assert changed_inputs(harness, job_id) == ["speed"], "a published speed must flag the clip"
    now = harness.job(job_id)["clip_inputs"]["now"]["pass_speed"]
    assert now["text"] == "37,2", "the speed a reprocess would draw"


def test_moving_the_speed_mark_flags_the_delivered_clip(harness):
    # Arrange: car 1's clip delivered with the mark at 4.0 s.
    set_up_avf(harness)
    job_id = delivered_avf_clip(harness)

    # Act: the reviewer moves the mark one frame later and saves.
    edit_review(harness, job_id, speed_mark_s=SPEED_MARK_S + 0.033)

    # Assert: flagged because of the speed only.
    assert changed_inputs(harness, job_id) == ["speed"], "a moved mark must flag the clip"


def test_clearing_the_speed_mark_flags_the_delivered_clip(harness):
    # Arrange: car 1's clip delivered with the speed.
    set_up_avf(harness)
    job_id = delivered_avf_clip(harness)

    # Act: the reviewer clears the mark ("Limpar").
    edit_review(harness, job_id, clear_speed_mark=True)

    # Assert: the clip on air has a speed the review no longer wants.
    assert changed_inputs(harness, job_id) == ["speed"], "a removed speed must flag the clip"
    assert harness.job(job_id)["clip_inputs"]["now"]["pass_speed"] is None, "none would be drawn"
