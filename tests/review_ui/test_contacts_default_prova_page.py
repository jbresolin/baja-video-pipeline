"""Admin → Contatos and the default prova (LIVESTACK-51 item 2), against the real orchestrator.

* Each contact row says how many of the sender's videos are waiting for
  review with no prova; saving the row then asks "Aplicar Enduro aos 2
  vídeos deste contato que ainda não têm prova?" (static/js/admin.js) and,
  on OK, sends `apply_default_prova_to_waiting=1` with the form.
* The hint under the field says what a default prova does, and that a
  "coringa" without one keeps leaving the prova empty.

Setup: `admin` is an admin's browser on a review UI wired to `harness`, a
whole orchestrator. Maria's phone writes to the bot without the 9th digit
(as older numbers do); she has two videos waiting, sent before she had a
default prova.
"""

import re

from orchestrator.testing.scenarios import ensure_prova, job_id_for_message, send_video
from review_ui.catalog import SCRIPT_TEXT
from tests.review_ui.admin_browser import follow, post_form

MARIA = "5519981234567"
MARIA_JID = "551981234567"


def maria_sends_two_videos(harness):
    job_ids = []
    for data in (b"first video", b"second video"):
        _, payload = send_video(harness, data, sender_phone=MARIA_JID, sender_name="Maria")
        job_ids.append(job_id_for_message(harness, payload["data"]["key"]["id"]))
    return job_ids


def test_the_row_counts_waiting_videos_and_the_page_explains_the_field(admin, harness):
    maria_sends_two_videos(harness)

    html = admin.get("/review/admin/contacts").text

    # One row for Maria, under the number with the 9th digit.
    assert html.count(f'action="/review/admin/contacts/{MARIA}"') >= 1
    assert MARIA_JID not in html
    assert "2 vídeos esperando revisão sem prova" in html
    assert 'data-waiting-without-prova="2"' in html
    assert "um “coringa”" in html


def test_saving_with_the_confirmation_fills_the_waiting_videos(admin, harness):
    job_ids = maria_sends_two_videos(harness)
    enduro = ensure_prova(harness, "Enduro")

    # The row's form as the browser sends it after "OK" on the question.
    response = post_form(
        admin,
        f"/review/admin/contacts/{MARIA}",
        display_name="Maria (filmagem)",
        default_prova_id=str(enduro["id"]),
        notes="",
        apply_default_prova_to_waiting="1",
    )

    page = follow(admin, response).text
    assert "a prova padrão foi aplicada aos vídeos" in page
    assert [harness.job(job_id)["prova"] for job_id in job_ids] == ["Enduro", "Enduro"]


def test_saving_without_it_changes_only_the_contact(admin, harness):
    # "Cancelar" on the question, or no script at all: the hidden field stays empty.
    job_ids = maria_sends_two_videos(harness)
    enduro = ensure_prova(harness, "Enduro")

    response = post_form(
        admin,
        f"/review/admin/contacts/{MARIA}",
        display_name="Maria (filmagem)",
        default_prova_id=str(enduro["id"]),
        notes="",
        apply_default_prova_to_waiting="",
    )

    assert "Contato atualizado." in follow(admin, response).text
    assert [harness.job(job_id)["prova"] for job_id in job_ids] == [None, None]


def test_the_question_says_the_prova_and_the_count():
    question = SCRIPT_TEXT["script.contacts.apply_default.other"].format(prova="Enduro", count=3)
    assert question == "Aplicar Enduro aos 3 vídeos deste contato que ainda não têm prova?"
    assert re.search(
        r"ao 1 vídeo .* não tem prova\?", SCRIPT_TEXT["script.contacts.apply_default.one"]
    )
