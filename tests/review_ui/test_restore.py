"""Tests for undoing a discard in the review UI (LIVESTACK-38 item 4).

The orchestrator keeps a discarded job's raw for a grace period (24 h by
default) and accepts the review action "restore". The UI must make that
findable and clear:
* the queue page has a "Descartados" quick filter;
* a discarded job's page offers a Restore button and says until when the
  raw is kept, or that the grace period is over.
"""

from datetime import timedelta

from review_ui.job_view import available_actions, discard_note
from shared.models import JobState, ReviewAction, utc_now
from tests.review_ui.sample_data import make_job


def discarded_job(job_id="job-discarded-1", **changes):
    """A job discarded from "ready", whose raw is kept for 20 more hours unless changed."""
    fields = {
        "discarded_from": JobState.READY,
        "discard_grace_ends_at": utc_now() + timedelta(hours=20),
        **changes,
    }
    return make_job(job_id, JobState.DISCARDED, **fields)


def test_the_queue_page_links_to_the_discarded_jobs(ui):
    html = ui.get("/review/").text
    # A quick filter that lists only discarded jobs (state=discarded).
    assert '<a class="chip" href="/review/?state=discarded">Descartados</a>' in html


def test_the_descartados_link_is_marked_when_it_is_the_filter(ui):
    html = ui.get("/review/?state=discarded").text
    assert 'class="chip active" href="/review/?state=discarded"' in html


def test_a_discarded_job_offers_only_restore():
    # Nothing else is allowed on a discarded job (not even save).
    assert available_actions(JobState.DISCARDED) == [ReviewAction.RESTORE]


def test_the_job_page_shows_restore_and_how_long_the_raw_is_kept(ui, fake_orchestrator):
    fake_orchestrator.add_job(discarded_job())

    html = ui.get("/review/jobs/job-discarded-1").text

    assert 'data-action="restore"' in html
    assert "até lá “Restaurar” volta o vídeo para pronto" in html


def test_after_the_grace_period_the_page_warns_that_the_raw_may_be_gone():
    job = discarded_job(discard_grace_ends_at=utc_now() - timedelta(minutes=5))
    note = discard_note(job, utc_now())
    # A warning, saying restore needs a holder that still has the raw.
    assert note.level == "warning"
    assert "só funciona enquanto algum worker ainda tiver uma cópia" in note.text


def test_no_discard_note_on_other_jobs():
    assert discard_note(make_job(), utc_now()) is None
