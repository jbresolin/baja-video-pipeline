"""The sender's messages and the received time on the review pages (LIVESTACK-49).

The orchestrator decides which messages belong to a video and in which
order (tested in tests/orchestrator/test_sender_messages.py). These tests
use the fake orchestrator, preloaded with what Bruno wrote around the job
"job-awaiting-1" (sample_data.bruno_messages), and check what the reviewer
sees: the collapsible "Mensagens de Bruno (6)", media shown by kind, the
"editada" / "apagada" marks, the folded "Antes deste vídeo", the queue's
speech bubble, and "Recebido" beside "Gravado" on the job page.
"""

from shared.models import SenderMessageRole
from tests.review_ui.sample_data import (
    bruno_messages,
    car_7_video,
    on_event_day,
    sender_message,
)


def job_page(ui, job_id="job-awaiting-1"):
    response = ui.get(f"/review/jobs/{job_id}")
    assert response.status_code == 200, response.text
    return response.text


def section_of(html):
    """The HTML of the messages section alone, so assertions do not match other parts."""
    start = html.index('id="sender-messages"')
    return html[start : html.index("</details>\n", html.index("messages-before", start)) + 10]


def test_the_section_is_closed_and_counts_every_message(ui, fake_orchestrator):
    fake_orchestrator.sender_messages["job-awaiting-1"] = bruno_messages()

    html = job_page(ui)
    # A <details> element without "open": the reviewer clicks to read.
    assert '<details class="sender-messages" id="sender-messages">' in html
    # The title says whose messages and how many: all six, "before" included.
    assert "<summary>Mensagens de Bruno (6)</summary>" in html


def test_texts_media_edits_and_deletions_are_shown_as_the_reviewer_needs(ui, fake_orchestrator):
    fake_orchestrator.sender_messages["job-awaiting-1"] = bruno_messages()

    section = section_of(job_page(ui))
    # The caption first, labelled as the video's own.
    assert section.index("legenda do vídeo") < section.index("segunda passagem")
    assert "carro 42" in section
    # An audio is not downloaded: just its kind.
    assert "(áudio)" in section
    # An edited message shows the new text and says it was edited.
    assert "o 42 de novo" in section and "editada" in section
    # A deleted one is still listed (something was said) but says "apagada".
    assert "apagada" in section
    # Messages from before this video are folded, with their own count.
    assert "<summary>Antes deste vídeo (1)</summary>" in section
    assert "vou mandar o 42 agora" in section


def test_a_reply_to_this_video_opens_the_section_and_is_marked(ui, fake_orchestrator):
    fake_orchestrator.sender_messages["job-awaiting-1"] = [
        sender_message(SenderMessageRole.REPLY, "errei, é a 3", 20),
    ]

    html = job_page(ui)
    # Someone answered this very video: the reviewer must not miss it.
    assert '<details class="sender-messages" id="sender-messages" open>' in html
    assert "resposta a este vídeo" in html and "errei, é a 3" in html


def test_a_filmmakers_text_is_shown_as_text_never_as_html(ui, fake_orchestrator):
    fake_orchestrator.sender_messages["job-awaiting-1"] = [
        sender_message(SenderMessageRole.AFTER, "<b>carro</b> 42", 1),
    ]

    html = job_page(ui)
    assert "&lt;b&gt;carro&lt;/b&gt; 42" in html and "<b>carro</b>" not in html


def test_no_messages_no_section_and_a_failed_call_still_shows_the_page(ui, fake_orchestrator):
    # Nothing written around this video: no empty box.
    assert 'id="sender-messages"' not in job_page(ui)

    # The orchestrator fails to answer: the page still works and says so.
    fake_orchestrator.sender_messages_fail = True
    html = job_page(ui)
    assert "Não foi possível carregar as mensagens do remetente." in html
    assert 'id="review-form"' in html


def test_the_job_header_shows_recorded_received_and_sender_side_by_side(ui, fake_orchestrator):
    # Filmed at 14:02 (the file's own time), received by the bot at 14:42.
    fake_orchestrator.add_job(car_7_video("car7-a", on_event_day(14, 2)))

    html = job_page(ui, "car7-a")
    start = html.index('class="job-origin')
    origin = html[start : html.index("</div>", start)]
    assert "Gravado <time" in origin and "26/03/2026 14:02:00</time> (horário do arquivo)" in origin
    assert "Recebido <time" in origin and "26/03/2026 14:42:00</time>." in origin
    assert "Enviado por Bruno" in origin


def test_the_queue_row_has_a_speech_bubble_with_the_count(ui, fake_orchestrator):
    fake_orchestrator.sender_messages["job-awaiting-1"] = bruno_messages()

    html = ui.get("/review/").text
    # The bubble with the count, and a title saying what it counts.
    assert 'title="6 mensagens do remetente sobre este vídeo">💬 6</span>' in html
    # The failed job has no messages: no bubble on its row.
    assert html.count("💬") == 1


def test_the_car_panel_shows_when_each_video_was_received(ui, fake_orchestrator):
    # Two videos of car 7, filmed 14:02 and 14:31; each reached the bot 40
    # minutes after it was filmed (sample_data.car_7_video).
    fake_orchestrator.add_job(car_7_video("car7-a", on_event_day(14, 2), passagem=1))
    fake_orchestrator.add_job(car_7_video("car7-b", on_event_day(14, 31)))

    html = job_page(ui, "car7-b")
    panel = html[html.index('id="passagem-help"') :]
    # Under each recording time, the received time, in the same short format.
    assert "recebido 14:42" in panel and "recebido 15:11" in panel
