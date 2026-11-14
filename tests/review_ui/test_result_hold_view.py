"""The words for waiting for the official result (LIVESTACK-62), without a server.

What this module is for: review_ui/result_hold_view.py decides whether the
job page has the box "Esperar o resultado oficial (até N min)" and whether
it starts shown, and writes the status strip's banner; event_log_view.py
writes the log lines; job_view.py puts the orchestrator's waiting reason
in pt-BR.

What these tests prove: those decisions and sentences, from Job and Event
records built by hand (the same models the orchestrator sends), and that
the waiting reason the UI recognises is the orchestrator's exact sentence.

What they do not cover: the pages themselves (test_result_hold_page.py).
T0 is 2026-10-25 14:00 UTC, 11:00 in São Paulo.
"""

from datetime import timedelta

from orchestrator.domain.result_hold import WAITING_FOR_OFFICIAL_RESULT
from review_ui.event_log_view import describe_kind
from review_ui.job_view import (
    WAITING_FOR_OFFICIAL_RESULT_REASON,
    action_buttons,
    describe_waiting_reason,
    explain_status,
)
from review_ui.result_hold_view import result_hold_note, result_wait_box
from shared.models import Event, JobState
from tests.review_ui.sample_data import T0, make_job, pass_clock, pass_result, pass_result_job


def held_job(**changes):
    """Car 26's Manobrabilidade video, ready and waiting for its result until 11:15."""
    fields = {
        "state": JobState.READY,
        "result_hold_until": T0 + timedelta(minutes=15),
        "result_wait_minutes": 15,
        "waiting_reason": WAITING_FOR_OFFICIAL_RESULT_REASON,
    }
    fields.update(changes)
    return pass_result_job("job-held-1", **fields)


# --- The box next to "Pronto" ---


def test_the_box_is_shown_ticked_with_the_settings_minutes_for_a_result_on():
    # Arrange: a Manobrabilidade video in review, result line on, the Settings at 20 min.
    job = pass_result_job(result_wait_minutes=20)

    # Act: decide the box for its buttons ("Salvar", "Pronto", "Descartar").
    box = result_wait_box(job, action_buttons(job.state))

    # Assert: there, shown, with the minutes in its label.
    assert box is not None, "a job with Pronto gets the box"
    assert box.shown is True, "the result line is on, so the box is shown"
    assert box.label == "Esperar o resultado oficial (até 20 min)"


def test_the_box_starts_hidden_when_neither_result_nor_clock_is_on():
    # Arrange: the result line switched off, and a clock that is off too.
    job = pass_result_job(
        result_wait_minutes=15,
        pass_result=pass_result(show=False),
        pass_clock=pass_clock(show=False),
    )

    # Act: decide the box.
    box = result_wait_box(job, action_buttons(job.state))

    # Assert: on the page (a toggle may be ticked later) but hidden.
    assert box is not None and box.shown is False, "nothing from the results would be drawn"


def test_a_clock_alone_shows_the_box():
    # Arrange: result line off, clock on.
    job = pass_result_job(
        result_wait_minutes=15, pass_result=pass_result(show=False), pass_clock=pass_clock()
    )

    # Act: decide the box.
    box = result_wait_box(job, action_buttons(job.state))

    # Assert: shown, since the clock waits for its official time.
    assert box.shown is True, "the clock alone is worth waiting for"


def test_no_box_without_pronto_or_reprocess_or_from_an_older_orchestrator():
    # Arrange: a ready job (only "Salvar" and "Descartar"), and a job from an
    # orchestrator older than LIVESTACK-62 (no minutes given).
    ready = pass_result_job(state=JobState.READY, result_wait_minutes=15)
    older = pass_result_job()

    # Act: decide both boxes.
    boxes = [result_wait_box(job, action_buttons(job.state)) for job in (ready, older)]

    # Assert: neither page gets a box.
    assert boxes == [None, None], "the box only sits next to Pronto / Salvar e reprocessar"


# --- The status strip while the render waits ---


def test_a_held_job_gets_the_banner_with_the_deadline():
    # Arrange: car 26's video held until 14:15 UTC.
    job = held_job()

    # Act: write the banner.
    note = result_hold_note(job)

    # Assert: the deadline in São Paulo time, hours and minutes.
    assert note.text == "Aguardando o resultado oficial (até 11:15).", "the banner says until when"
    assert "às 11:15 sem ele" in note.hint, "and what happens at the deadline"


def test_the_banner_replaces_the_generic_waiting_note():
    # Arrange: car 26's video held for its result.
    job = held_job()

    # Act: write the status strip's sentences.
    notes = [note.text for note in explain_status(job, {}, T0)]

    # Assert: no "Aguardando o resultado oficial." note beside the banner.
    assert "Aguardando o resultado oficial." not in notes, "the banner says it once"


def test_a_job_that_is_not_held_has_no_banner():
    # Arrange: a plain ready job.
    job = make_job("job-ready-1", JobState.READY)

    # Act: write the banner.
    note = result_hold_note(job)

    # Assert: nothing.
    assert note is None, "only a held job gets the banner"


def test_the_waiting_reason_is_the_orchestrators_sentence_in_pt_br():
    # Arrange: the sentence the orchestrator writes on a held job.
    reason = WAITING_FOR_OFFICIAL_RESULT

    # Act: put it in words for the queue.
    described = describe_waiting_reason(reason)

    # Assert: recognised (not shown as English), and the same sentence on both sides.
    assert reason == WAITING_FOR_OFFICIAL_RESULT_REASON, "the UI must know the exact sentence"
    assert (described.text, described.detail) == ("Aguardando o resultado oficial.", None)


# --- The event log ---


def test_the_held_line_says_until_when():
    # Arrange: the event the orchestrator records on "Pronto" with the box ticked.
    held = Event(
        id=7,
        at=T0,
        actor="reviewer:ana",
        kind="result_hold_started",
        job_id="job-held-1",
        details={"until": (T0 + timedelta(minutes=15)).isoformat(), "waits_for": ["result"]},
    )

    # Act: put it in words.
    words, is_kind_name = describe_kind(held)

    # Assert: the deadline in São Paulo time.
    assert words == "renderização esperando o resultado oficial até 11:15"
    assert is_kind_name is False


def test_the_held_line_without_a_deadline_still_reads_well():
    # Arrange: the same event with no details (never written so; a safety net).
    held = Event(id=7, at=T0, actor="system", kind="result_hold_started", job_id="job-held-1")

    # Act: put it in words.
    words, _ = describe_kind(held)

    # Assert: the sentence without a time.
    assert words == "renderização esperando o resultado oficial"
