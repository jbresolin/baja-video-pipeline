"""Tests for the plain functions behind the pages (no web requests involved).

job_view: which actions a state allows, the status strip, intake, frame rate.
queue_view / clips_and_fleet_view: filters, studio paths, fleet ordering
and each worker's recent events.
(The event log's own wording is tested in test_event_log_view.py.)
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
from review_ui.job_view import (
    available_actions,
    explain_status,
    frame_rate_for_stepping,
    intake_text,
)
from review_ui.queue_view import DEFAULT_QUEUE_STATES, distinct_provas, parse_queue_filter
from shared.models import JobState, ReviewAction, TaskKind
from shared.models.job import Lease
from tests.review_ui.sample_data import (
    MASTER_ID,
    T0,
    VOLUNTEER_ID,
    delivered_job,
    failed_job,
    make_fleet,
    make_job,
)
from tests.review_ui.sample_events import make_events

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


# --- Intake and frame rate ---


def test_intake_text_says_who_sent_the_raw_and_how():
    # The sample job came from "Bruno" as a WhatsApp document named VID_0042.mp4.
    assert intake_text(make_job()) == "Bruno as a WhatsApp document (VID_0042.mp4)"


def test_intake_text_without_sender_or_filename():
    job = make_job()
    source = job.source.model_copy(update={"sender_name": None, "original_filename": None})

    assert intake_text(job.model_copy(update={"source": source})) == (
        "an unknown sender as a WhatsApp document"
    )


def test_frame_rate_comes_from_the_job():
    # A holder measured the proxy at 29.97 fps: the player steps by 1/29.97 s.
    assert frame_rate_for_stepping(make_job(frame_rate=29.97)) == 29.97


def test_frame_rate_falls_back_to_30_when_unknown():
    # No proxy measured yet (frame_rate is None): phone footage is ~30 fps.
    assert make_job().frame_rate is None
    assert frame_rate_for_stepping(make_job()) == 30.0


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


def test_worker_rows_carry_their_recent_events_newest_first():
    # Setup: hand each worker the events that mention it, the way the fleet
    # route does after calling GET /events?worker_id=… once per worker.
    events = make_events()
    by_worker = {
        worker_id: [event for event in events if event.worker_id == worker_id]
        for worker_id in (MASTER_ID, VOLUNTEER_ID)
    }

    rows = build_worker_rows(make_fleet(), by_worker)

    master, volunteer = rows
    # Newest first: the volunteer's last event is the system expiring its
    # lease (T0+35 min); its first is its enrollment by the admin.
    assert volunteer.recent_events[0].what == "task timed out (no heartbeat)"
    assert volunteer.recent_events[0].actor == "system"
    assert volunteer.recent_events[-1].what == "worker enrolled"
    # Actors that are workers are shown by name, from the fleet itself.
    assert master.recent_events[0].actor == "joao-desktop"


def test_worker_rows_mark_events_that_could_not_be_loaded():
    # The volunteer's events failed to load (None); the master's were empty.
    rows = build_worker_rows(make_fleet(), {MASTER_ID: [], VOLUNTEER_ID: None})

    master, volunteer = rows
    # [] = "no events yet"; None = "unavailable". The template words them apart.
    assert master.recent_events == []
    assert volunteer.recent_events is None


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
