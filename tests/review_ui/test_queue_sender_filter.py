"""The queue's "Enviado por" filter (LIVESTACK-51 item 5).

João asked to filter the queue by who sent the videos. The select lists
the senders who have videos in the current view (by the contact's name,
else the number), it combines with the other filters, and it is kept in
the URL like them, so a link or the auto-refresh keeps it.

Setup: a fake orchestrator (fake_orchestrator.py) with videos from Maria
(Campinas, DDD 19) and Alessander (São Paulo, DDD 11), in two provas. The
fake gives each queue item the JID's digits as `sender_phone` and the
WhatsApp name as `sender_display_name`, as the orchestrator does when the
contact has no name of its own.
"""

import re

import pytest

from review_ui.queue_view import parse_queue_filter
from shared.models import IntakeSource, ReceivedAs
from tests.review_ui.sample_data import make_job

MARIA = "5519981234567"
ALESSANDER = "5511987650000"


def from_sender(job_id, phone, name, **changes):
    """A job awaiting review sent by `phone` with WhatsApp name `name`."""
    source = IntakeSource(
        message_id=f"msg-{job_id}",
        sender_jid=f"{phone}@s.whatsapp.net",
        sender_name=name,
        received_as=ReceivedAs.DOCUMENT,
    )
    return make_job(job_id, source=source, **changes)


@pytest.fixture
def two_senders(fake_orchestrator):
    # Replace the default jobs with four videos: Maria filmed Enduro and
    # Suspensão, Alessander filmed Enduro twice ("coringa", no fixed prova).
    fake_orchestrator.jobs.clear()
    fake_orchestrator.add_job(from_sender("maria-enduro", MARIA, "Maria (filmagem)", prova="Enduro"))
    fake_orchestrator.add_job(from_sender("maria-susp", MARIA, "Maria (filmagem)", prova="Suspensão"))
    fake_orchestrator.add_job(from_sender("ale-1", ALESSANDER, "Alessander", prova="Enduro"))
    fake_orchestrator.add_job(from_sender("ale-2", ALESSANDER, "Alessander", prova="Enduro"))
    return fake_orchestrator


def listed_jobs(html: str) -> set[str]:
    return set(re.findall(r'href="/review/jobs/([^"]+)"', html))


def test_the_select_lists_the_senders_in_the_view_by_name(ui, two_senders):
    html = ui.get("/review/").text

    # One option per sender, by name, alphabetical; the value is the number.
    options = re.findall(r'<option value="(\d+)" >([^<]+)</option>', html)
    assert options == [(ALESSANDER, "Alessander"), (MARIA, "Maria (filmagem)")]
    assert "Enviado por" in html and "Qualquer pessoa" in html


def test_filtering_by_sender_alone(ui, two_senders):
    html = ui.get("/review/", params={"sender": MARIA}).text

    # Only Maria's two videos; her option is the selected one.
    assert listed_jobs(html) == {"maria-enduro", "maria-susp"}
    assert f'<option value="{MARIA}" selected>Maria (filmagem)</option>' in html
    # Both senders stay in the select, so switching is one click.
    assert f'<option value="{ALESSANDER}" >Alessander</option>' in html


def test_filtering_by_sender_and_prova_together(ui, two_senders):
    html = ui.get("/review/", params={"sender": MARIA, "prova": "Enduro"}).text

    # Maria AND Enduro: only one video matches both.
    assert listed_jobs(html) == {"maria-enduro"}


def test_the_filter_is_kept_in_the_url_and_the_auto_refresh(ui, two_senders):
    html = ui.get("/review/", params={"sender": ALESSANDER, "prova": "Enduro"}).text

    # The rows are refreshed every few seconds from this URL: it must keep
    # the sender, or the next refresh would show everyone again.
    poll_url = re.search(r'data-poll-url="([^"]+)"', html).group(1)
    assert f"sender={ALESSANDER}" in poll_url
    rows = ui.get(poll_url.replace("&amp;", "&")).text
    assert listed_jobs(rows) == {"ale-1", "ale-2"}


def test_a_sender_with_no_video_in_the_view_still_shows_as_chosen(ui, two_senders):
    # A link from earlier, for a sender whose videos all left this view: the
    # list is empty, and the select keeps the choice so it can be cleared.
    html = ui.get("/review/", params={"sender": "5511900000000"}).text

    assert listed_jobs(html) == set()
    assert '<option value="5511900000000" selected>5511900000000</option>' in html


def test_the_sender_round_trips_through_the_query_string():
    queue_filter = parse_queue_filter(["awaiting_review"], "Enduro", None, None, MARIA)

    assert queue_filter.query_string() == f"state=awaiting_review&prova=Enduro&sender={MARIA}"
