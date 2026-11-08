"""What reviewers see of duplicate videos (LIVESTACK-51 item 3).

The orchestrator discards a copy of a video it already had (tests/
orchestrator/test_duplicate_videos.py). The review UI shows it:

* on the copy's page: "Descartado automaticamente: duplicado de <link to
  the first job>" (and "Restaurar" works as for any discard);
* on the first job's page: "Também recebido de: Alessander, 11:05", each a
  link to the copy;
* in the queue (e.g. "Descartados"): a "duplicado" badge;
* on Configurações: the bot's reply, with `{hora}` explained.

Setup: `ui` is a signed-in browser against a fake orchestrator
(fake_orchestrator.py); `admin` is an admin's browser against the real
orchestrator (`harness`). Times: T0 = 14:00 UTC = 11:00 in São Paulo.
"""

import re
from datetime import timedelta

from shared.models import DuplicateCopy, JobState
from tests.review_ui.admin_browser import post_form
from tests.review_ui.sample_data import T0, make_job


def add_pair(fake_orchestrator):
    """Maria's first copy (awaiting review) and Alessander's forward, discarded."""
    copy = DuplicateCopy(
        job_id="copy-alessander",
        state=JobState.DISCARDED,
        sender_name="Alessander",
        received_at=T0 + timedelta(minutes=5),
    )
    fake_orchestrator.add_job(make_job("first-maria", duplicates=[copy]))
    fake_orchestrator.add_job(
        make_job(
            "copy-alessander",
            JobState.DISCARDED,
            duplicate_of="first-maria",
            discarded_from=JobState.RECEIVED,
            discard_grace_ends_at=T0 + timedelta(hours=24),
        )
    )


def test_the_copy_says_it_is_a_duplicate_and_links_the_first_job(ui, fake_orchestrator):
    add_pair(fake_orchestrator)

    html = ui.get("/review/jobs/copy-alessander").text

    note = re.search(r'<p class="note warning" id="duplicate-of">(.*?)</p>', html, re.S)
    assert note is not None
    assert "Descartado automaticamente: duplicado de" in note.group(1)
    assert '<a href="/review/jobs/first-maria"><code>first-ma</code></a>' in note.group(1)
    # Restaurar is offered, as for any discarded job.
    assert 'data-action="restore"' in html


def test_the_first_job_lists_who_else_sent_it(ui, fake_orchestrator):
    add_pair(fake_orchestrator)

    html = ui.get("/review/jobs/first-maria").text

    listed = re.search(r'<p class="note info" id="duplicates">(.*?)</p>', html, re.S).group(1)
    assert "Também recebido de:" in listed
    # The time is short: the copy came the same day as this video.
    assert '<a href="/review/jobs/copy-alessander">Alessander, 11:05</a>' in listed
    # The first job is no copy itself.
    assert 'id="duplicate-of"' not in html


def test_the_status_strip_refresh_keeps_the_lines(ui, fake_orchestrator):
    # The strip is refreshed every few seconds; the lines must survive it.
    add_pair(fake_orchestrator)

    panel = ui.get("/review/jobs/first-maria/panel").text

    assert 'id="duplicates"' in panel


def test_the_queue_marks_duplicates(ui, fake_orchestrator):
    add_pair(fake_orchestrator)

    html = ui.get("/review/", params={"state": "discarded"}).text

    assert html.count(">duplicado</span>") == 1


def test_the_settings_page_edits_the_reply_to_duplicates(admin, harness):
    html = admin.get("/review/admin/settings").text
    assert '<textarea id="duplicate_video_message" name="duplicate_video_message"' in html
    assert "⚠️ Este vídeo já tinha sido recebido (às {hora}). Não precisa reenviar." in html
    assert "<code>{hora}</code> vira a hora" in html

    # Saving the form (every field, as the browser sends it) with a new text.
    fields = dict(re.findall(r'<input id="(\w+)" name="\w+"[^>]*?value="([^"]*)"', html))
    current = harness.context.admin_settings.current
    fields.update(
        intake_mode="open",
        clip_team_name_style="short",
        clip_school_name_style="short",
        welcome_message=current.welcome_message,
        vertical_video_message=current.vertical_video_message,
        duplicate_video_message="Esse já chegou às {hora} 👍",
    )
    post_form(admin, "/review/admin/settings", **fields)

    assert harness.context.admin_settings.current.duplicate_video_message == (
        "Esse já chegou às {hora} 👍"
    )
