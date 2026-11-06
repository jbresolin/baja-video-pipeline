"""process: the worker says what each clip was rendered from (LIVESTACK-43).

Before it uploads or pushes a rendered clip, the worker sends
PUT /jobs/{id}/render-inputs under its process lease: the cuts and review
fields it rendered, the texts the overlay shows, the overlay theme version
(SHA-256 of its vendored tokens.css) and the clip's SHA-256. The
orchestrator keeps that with the clip version, so the review UI can warn
when a reviewer later edits a job whose clip is already on air.

The report must never cost a delivery: if the orchestrator refuses it
(an older orchestrator answers 404), the clip is delivered anyway.
"""

import hashlib

from tests.worker.test_process import hold_raw, process_task
from worker import overlay_tokens


def test_the_report_comes_before_the_upload_with_what_was_rendered(harness, raw_bytes):
    job = hold_raw(harness, raw_bytes)
    task = process_task(job, team_name="Baja UFMG", school_short="UFMG", show_pass=False)

    harness.claim_and_run(task)

    # One report, for this job, describing exactly the delivered file.
    [(job_id, report)] = harness.orchestrator.render_inputs
    delivered = harness.orchestrator.stored_upload("outbox", job.job_id).read_bytes()
    assert job_id == job.job_id
    assert report.clip_sha256 == hashlib.sha256(delivered).hexdigest()
    # The values the render used: the task's, as drawn on the overlay.
    assert (report.cut_in_s, report.cut_out_s) == (2.0, 9.5)
    assert (report.prova, report.car_number, report.passagem) == ("Enduro", 42, 3)
    assert report.show_pass is False
    assert (report.overlay_team_name, report.overlay_school_name) == ("Baja UFMG", "UFMG")
    assert report.raw_sha256 == job.sha256
    # Which overlay look this worker draws: the hash of its tokens.css.
    assert report.overlay_theme_version == overlay_tokens.THEME_VERSION
    # And it was sent before the first chunk of the clip, so the
    # orchestrator has it when the upload completes.
    paths = harness.orchestrator.request_log
    report_at = paths.index(f"PUT /jobs/{job.job_id}/render-inputs")
    first_chunk_at = paths.index(f"POST /jobs/{job.job_id}/clip")
    assert report_at < first_chunk_at


def test_the_theme_version_is_the_sha256_of_the_vendored_tokens_css():
    from importlib import resources

    css = resources.files("worker.overlay_theme").joinpath("tokens.css").read_bytes()
    assert hashlib.sha256(css).hexdigest() == overlay_tokens.THEME_VERSION


def test_a_refused_report_does_not_stop_the_delivery(harness, raw_bytes):
    # An orchestrator from before contract 2.4.0 has no such route: 404.
    harness.orchestrator.render_inputs_refusal = (404, "not_found")
    job = hold_raw(harness, raw_bytes)

    harness.claim_and_run(process_task(job))

    # The clip still reached the outbox and nothing was reported as failed.
    assert harness.orchestrator.stored_upload("outbox", job.job_id).exists()
    assert harness.orchestrator.failures == []
