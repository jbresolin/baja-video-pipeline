"""The passagem help on the job page, worked out without a browser (LIVESTACK-45).

A reviewer must say whether a video is car 7's 1st, 2nd or nth passagem in
"Suspensão". The job page lists the car's other videos in filming order
and, from that list, review_ui/passagem_help_view.py works out:

* the summary, "Passagens já registradas: 1 (14:02), 2 (14:31)";
* a suggestion: one more than the passagem of the last numbered video
  filmed before this one (never filled in without a click);
* warnings for the passagem typed: already used by another video, or out
  of filming order. Warnings never block.

These tests call that module directly with ready-made API objects. Times
are on the track, 2026-03-26 (São Paulo).
"""

from review_ui.passagem_help_view import passagem_help
from shared.models import JobSummary, RecordedAtSource
from tests.review_ui.sample_data import car_7_video, on_event_day


def summaries(*jobs):
    """What GET /review/car-videos returns: the jobs as list entries."""
    return [JobSummary.from_job(job) for job in jobs]


# Two passes already numbered by other reviewers.
PASS_1 = car_7_video("job-p1", on_event_day(14, 2), passagem=1)
PASS_2 = car_7_video("job-p2", on_event_day(14, 31), passagem=2)


def help_for(this_video, others, typed_passagem=None):
    return passagem_help(this_video, "Suspensão", 7, summaries(*others), typed_passagem)


def test_no_other_videos_suggests_passagem_1():
    this_video = car_7_video("job-this", on_event_day(14, 2))

    panel = help_for(this_video, [])

    # Only this video in the list, said plainly, and passagem 1 suggested.
    assert [row.is_this_video for row in panel.rows] == [True]
    assert panel.has_others is False
    assert panel.registered_text == "Nenhuma passagem registrada ainda."
    assert panel.suggestion == 1
    assert panel.suggestion_text == (
        "Sugestão: passagem 1 — é o único vídeo deste carro nesta prova"
    )
    assert panel.warnings == []


def test_filmed_after_passes_1_and_2_suggests_3():
    # This video was filmed at 14:50, after both numbered passes.
    this_video = car_7_video("job-this", on_event_day(14, 50))

    panel = help_for(this_video, [PASS_2, PASS_1])

    # The list is in filming order with this video last, whatever order
    # the videos were given in.
    assert [row.video.id for row in panel.rows] == ["job-p1", "job-p2", "job-this"]
    assert [row.time_text for row in panel.rows] == ["14:02", "14:31", "14:50"]
    assert panel.registered_text == "Passagens já registradas: 1 (14:02), 2 (14:31)"
    assert panel.suggestion == 3
    assert panel.suggestion_text == (
        "Sugestão: passagem 3 — gravado depois da passagem 2, de 14:31"
    )
    # Nothing typed yet: nothing to warn about.
    assert panel.warnings == []


def test_typing_the_suggestion_gives_no_warning():
    this_video = car_7_video("job-this", on_event_day(14, 50))
    assert help_for(this_video, [PASS_1, PASS_2], typed_passagem=3).warnings == []


def test_filmed_between_1_and_2_but_numbered_3_breaks_the_order():
    # Filmed at 14:15, between passagem 1 (14:02) and passagem 2 (14:31),
    # yet the reviewer typed 3.
    this_video = car_7_video("job-this", on_event_day(14, 15))

    panel = help_for(this_video, [PASS_1, PASS_2], typed_passagem=3)

    assert [row.video.id for row in panel.rows] == ["job-p1", "job-this", "job-p2"]
    assert panel.warnings == [
        "Passagem 3 fora da ordem: este vídeo foi gravado antes da passagem 2, de 14:31."
    ]
    # The order says 2, but 2 is taken: no suggestion, only the reason, so
    # the reviewer checks both videos instead of clicking a wrong number.
    assert panel.suggestion is None
    assert panel.suggestion_text == (
        "Sem sugestão: pela ordem de gravação seria a passagem 2, que já é o vídeo de 14:31. "
        "Confira os dois."
    )


def test_a_number_already_used_is_a_warning():
    # Filmed after both, but the reviewer typed 2, which is the 14:31 video.
    this_video = car_7_video("job-this", on_event_day(14, 50))

    panel = help_for(this_video, [PASS_1, PASS_2], typed_passagem=2)

    assert panel.warnings == ["A passagem 2 já é o vídeo de 14:31."]


def test_numbered_lower_than_a_pass_filmed_before_it_breaks_the_order():
    # Filmed after passagem 2, numbered 1: both "already used" and "out of order".
    this_video = car_7_video("job-this", on_event_day(14, 50))

    panel = help_for(this_video, [PASS_1, PASS_2], typed_passagem=1)

    assert panel.warnings == [
        "A passagem 1 já é o vídeo de 14:02.",
        "Passagem 1 fora da ordem: este vídeo foi gravado depois da passagem 2, de 14:31.",
    ]


def test_this_video_in_the_list_uses_what_the_form_says_now():
    # The orchestrator's list has this video too (saved as passagem 2); the
    # reviewer has typed 3 since, without saving. The panel uses 3.
    saved = car_7_video("job-this", on_event_day(14, 50), passagem=2)

    panel = help_for(saved, [PASS_1, saved], typed_passagem=3)

    assert [row.video.id for row in panel.rows] == ["job-p1", "job-this"]
    assert panel.rows[1].video.passagem == 3
    # Its own saved number does not count as "used by another video".
    assert panel.registered_text == "Passagens já registradas: 1 (14:02)"
    assert panel.warnings == []


def test_unnumbered_videos_are_listed_but_do_not_count():
    # Another video of car 7 nobody has numbered yet, filmed before this one.
    unnumbered = car_7_video("job-other", on_event_day(14, 10))
    this_video = car_7_video("job-this", on_event_day(14, 50))

    panel = help_for(this_video, [unnumbered])

    assert [row.video.id for row in panel.rows] == ["job-other", "job-this"]
    assert panel.registered_text == "Nenhuma passagem registrada ainda."
    assert panel.suggestion == 1
    assert panel.suggestion_text == (
        "Sugestão: passagem 1 — nenhum vídeo deste carro tem passagem ainda"
    )


def test_filmed_before_every_numbered_pass_suggests_1_when_free():
    # Passagem 2 is numbered, 1 is not yet; this video came first.
    this_video = car_7_video("job-this", on_event_day(13, 50))

    panel = help_for(this_video, [PASS_2])

    assert panel.suggestion == 1
    assert panel.suggestion_text == (
        "Sugestão: passagem 1 — gravado antes de todas as passagens registradas"
    )


def test_send_times_are_marked_and_the_order_called_approximate():
    # PASS_2's file had no creation_time: its time is when it was sent.
    sent_only = car_7_video(
        "job-p2", on_event_day(14, 31), passagem=2, source=RecordedAtSource.WHATSAPP
    )
    this_video = car_7_video("job-this", on_event_day(14, 50))

    panel = help_for(this_video, [PASS_1, sent_only])

    assert [row.time_is_hint for row in panel.rows] == [False, True, False]
    assert panel.order_is_approximate is True


def test_exact_times_only_means_an_exact_order():
    this_video = car_7_video("job-this", on_event_day(14, 50))
    assert help_for(this_video, [PASS_1, PASS_2]).order_is_approximate is False


def test_a_time_on_another_day_shows_its_date():
    # A pass filmed the day before (a re-run carried over) shows the date;
    # those on this video's day show only the time.
    day_before = car_7_video("job-p1", on_event_day(14, 2).replace(day=25), passagem=1)
    this_video = car_7_video("job-this", on_event_day(14, 50))

    panel = help_for(this_video, [day_before])

    assert [row.time_text for row in panel.rows] == ["25/03/2026 14:02", "14:50"]
