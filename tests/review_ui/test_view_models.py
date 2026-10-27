"""Tests for the plain functions behind the pages (no web requests involved).

job_view: which actions a state allows, the status strip, the event log.
queue_view / clips_and_fleet_view: filters, studio paths, fleet ordering.
formatting: the small text helpers used by the templates.
"""

from datetime import timedelta

import pytest

from review_ui import formatting
from review_ui.clips_and_fleet_view import (
    build_worker_rows,
    jobs_by_state_in_order,
    studio_path,
)
from review_ui.job_view import available_actions, build_timeline, explain_status
from review_ui.queue_view import DEFAULT_QUEUE_STATES, distinct_provas, parse_queue_filter
from shared.models import JobState, ReviewAction, TaskKind
from shared.models.job import Lease
from tests.review_ui.sample_data import (
    MASTER_ID,
    T0,
    delivered_job,
    failed_job,
    make_fleet,
    make_job,
)

# --- Actions per state (docs/api.md §7.1 "Review update") ---


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        (JobState.AWAITING_REVIEW, ["save", "ready", "discard"]),
        (JobState.READY, ["save", "discard"]),
        (JobState.CLAIMED, ["save", "reprocess"]),
        (JobState.DONE, ["save", "reprocess"]),
        (JobState.FAILED, ["save", "retry", "discard"]),
        (JobState.RECEIVED, ["save"]),
        (JobState.DISCARDED, []),
    ],
)
def test_available_actions_follow_the_api_table(state, expected):
    assert [action.value for action in available_actions(state)] == expected


def test_every_action_is_available_in_some_state():
    # Guards against an action that no button could ever trigger.
    offered = {action for state in JobState for action in available_actions(state)}
    assert offered == set(ReviewAction)


# --- Status strip ---


def test_status_strip_for_a_low_res_job_mentions_the_resend_request():
    notes = explain_status(make_job(low_res=True), {})

    assert any("resend it as a document" in note.text for note in notes)


def test_status_strip_names_workers_with_active_leases():
    job = make_job(
        state=JobState.CLAIMED,
        leases=[
            Lease(
                lease_id="l1",
                job_id="job-awaiting-1",
                worker_id=MASTER_ID,
                task_kind=TaskKind.PROCESS,
                acquired_at=T0,
                expires_at=T0 + timedelta(seconds=45),
            )
        ],
    )

    notes = explain_status(job, {MASTER_ID: "joao-desktop"})

    assert any(note.text == "joao-desktop is rendering the clip." for note in notes)


def test_status_strip_says_when_no_worker_has_a_copy_yet():
    job = make_job(state=JobState.RECEIVED, holders=[], proxy_available=False)

    notes = explain_status(job, {})

    texts = [note.text for note in notes]
    assert "No worker has a verified copy of the raw yet." in texts
    # A job still in "received" has no proxy by design: no eviction warning.
    assert not any("evicted" in text for text in texts)


def test_failure_note_is_an_error():
    notes = explain_status(failed_job(), {})

    assert [note.level for note in notes][:2] == ["error", "error"]


def test_delivered_job_shows_where_the_clip_is():
    notes = explain_status(delivered_job(), {})

    assert notes[0].level == "success"
    assert notes[-1].text == "On the studio box at suspensao/7/1.mp4."


# --- Event log ---


def test_timeline_is_newest_first_and_starts_with_the_last_change():
    timeline = build_timeline(delivered_job(), {})

    moments = [event.at for event in timeline]
    assert moments == sorted(moments, reverse=True)
    assert timeline[0].text == "Last change: now done, v3"


# --- Queue filter ---


def test_queue_filter_defaults_and_ordering():
    # No states → the default "needs a human" states.
    assert parse_queue_filter([], None, None).states == DEFAULT_QUEUE_STATES
    # Duplicates are dropped and states are put in pipeline order.
    chosen = parse_queue_filter(["done", "ready", "done"], " Enduro ", "1")
    assert chosen.states == [JobState.READY, JobState.DONE]
    assert chosen.prova == "Enduro"
    assert chosen.flagged_only is True
    assert chosen.query_string() == "state=ready&state=done&prova=Enduro&flagged=1"


def test_distinct_provas_compares_like_folder_names():
    provas = ["Suspensão", "suspensao", None, "Enduro", "ENDURO"]

    # The first spelling of each prova is kept, in order.
    assert distinct_provas(provas) == ["Suspensão", "Enduro"]


# --- Clips and fleet ---


def test_studio_path_without_and_with_a_clip_root():
    assert studio_path("enduro/42/3.mp4", "") == "enduro/42/3.mp4"
    assert studio_path("enduro/42/3.mp4", "/srv/clips/") == "/srv/clips/enduro/42/3.mp4"
    assert studio_path("enduro/42/3.mp4", "D:\\Clips\\") == "D:\\Clips\\enduro\\42\\3.mp4"


def test_worker_rows_compute_storage_use():
    rows = build_worker_rows(make_fleet())

    master = rows[0]
    assert master.entry.worker.name == "joao-desktop"
    # 500 GB cap with 300 GB free → 200 GB used, 40 %.
    assert master.used_bytes == 200 * 1_000_000_000
    assert master.used_percent == 40


def test_job_counts_include_zero_states_and_keep_unknown_ones():
    fleet = make_fleet().model_copy(update={"jobs_by_state": {"done": 3, "brand_new_state": 1}})

    counts = dict(jobs_by_state_in_order(fleet))

    assert counts["awaiting_review"] == 0
    assert counts["done"] == 3
    # A state added to the API later still shows up instead of vanishing.
    assert counts["brand_new_state"] == 1


# --- Formatting ---


def test_formatting_helpers():
    assert formatting.format_bytes(None) == "—"
    assert formatting.format_bytes(999) == "999 B"
    assert formatting.format_bytes(1_500_000_000) == "1.5 GB"
    assert formatting.format_timecode(62.5) == "1:02.500"
    assert formatting.format_duration(62.5) == "1:02"
    assert formatting.iso_utc(T0) == "2026-10-25T14:00:00Z"
    assert formatting.age_text(T0 - timedelta(seconds=30), T0) == "30 s ago"
    assert formatting.age_text(T0 - timedelta(hours=2), T0) == "2 h ago"
    assert formatting.age_text(None, T0) == "never"
    assert formatting.percent(30, 0) == 0
    assert formatting.percent(150, 100) == 100
    assert formatting.state_label("awaiting_review") == "awaiting review"
