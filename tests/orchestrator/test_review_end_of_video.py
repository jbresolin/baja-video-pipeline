"""A cut-out or a mark a hair past the end of the video means "the end".

What this is for: the job page writes times with 3 decimals, rounded to
the nearest millisecond, but a video's duration is rarely a whole number
of milliseconds. On 2026-11-20 a video of 11.5717 s, with its cut-out left
at the end, came back from the page as 11.572 and the save was refused:
"cut_out_s is past the end of the video (11.5717 s)". The page's player
can also put "the end" at the proxy's duration, a frame off the raw's.

What these tests prove:
  * a cut-out up to a tenth of a second past the end is saved as the end
    (the video's duration itself), so the save goes through;
  * the clock's largada mark gets the same treatment;
  * a cut-out clearly past the end is still refused, with the duration;
  * a cut-out inside the video is stored exactly as typed.

Not covered: the speed mark (same code path as the largada, a list of
field names in domain/review.py) and the page itself, which is unchanged.
"""

import pytest

from orchestrator.testing.scenarios import hold_raw, make_proxy, receive_job
from shared.models import WorkerRole

# The test video's duration, as hold_raw reports it.
VIDEO_DURATION_S = 12.0


@pytest.fixture
def job_in_review(harness):
    """A 12 s video waiting for review, held by the master with its proxy made."""
    master = harness.enroll(WorkerRole.MASTER, "desktop-estudio", tailnet_address="100.70.0.9")
    job_id = receive_job(harness)
    hold_raw(harness, master, job_id)
    make_proxy(harness, master, job_id)
    return job_id


def save_review(harness, job_id, **fields):
    """Press "Salvar" on the job page with these fields."""
    body = {"action": "save", "expected_version": harness.job(job_id)["version"], **fields}
    return harness.client.patch(f"/jobs/{job_id}/review", json=body, auth=harness.reviewer_auth)


@pytest.mark.parametrize(
    "typed_cut_out_s",
    [
        # The case seen on the VPS: the end rounded up to the next millisecond.
        pytest.param(12.0003, id="rounded-up-to-the-millisecond"),
        # The proxy one frame (at 30 fps) longer than the raw.
        pytest.param(12.033, id="one-frame-past-the-end"),
        # The furthest that still counts as the end.
        pytest.param(12.1, id="exactly-the-tolerance"),
    ],
)
def test_a_cut_out_just_past_the_end_is_saved_as_the_end(harness, job_in_review, typed_cut_out_s):
    # Arrange: a 12 s video waiting for review.
    job_id = job_in_review

    # Act: save a cut-out a little past the end.
    response = save_review(harness, job_id, cut_in_s=0.0, cut_out_s=typed_cut_out_s)

    # Assert: the save goes through and the cut-out stored is the end itself.
    assert response.status_code == 200, response.text
    assert harness.job(job_id)["cut_out_s"] == VIDEO_DURATION_S, (
        "a cut-out a hair past the end must be stored as the video's duration"
    )


def test_a_largada_just_past_the_end_is_saved_as_the_end(harness, job_in_review):
    # Arrange: a 12 s video waiting for review.
    job_id = job_in_review

    # Act: save the clock's largada mark rounded up past the end.
    response = save_review(harness, job_id, largada_s=12.0004)

    # Assert: saved, and the mark is the last moment of the video.
    assert response.status_code == 200, response.text
    assert harness.job(job_id)["largada_s"] == VIDEO_DURATION_S, (
        "a mark a hair past the end must be stored as the video's duration"
    )


def test_a_cut_out_clearly_past_the_end_is_still_refused(harness, job_in_review):
    # Arrange: a 12 s video waiting for review.
    job_id = job_in_review

    # Act: save a cut-out half a second past the end (a typing mistake).
    response = save_review(harness, job_id, cut_in_s=0.0, cut_out_s=12.5)

    # Assert: refused, saying how long the video is, and nothing is stored.
    assert response.status_code == 422, "half a second past the end is a mistake, not rounding"
    assert "past the end of the video" in response.text, "the error says what is wrong"
    assert harness.job(job_id)["cut_out_s"] is None, "a refused save stores nothing"


def test_a_cut_out_inside_the_video_is_stored_as_typed(harness, job_in_review):
    # Arrange: a 12 s video waiting for review.
    job_id = job_in_review

    # Act: save a cut-out a little before the end.
    response = save_review(harness, job_id, cut_in_s=0.0, cut_out_s=11.9)

    # Assert: the cut-out is exactly what was typed (only times past the end move).
    assert response.status_code == 200, response.text
    assert harness.job(job_id)["cut_out_s"] == 11.9, "a time inside the video is never changed"
