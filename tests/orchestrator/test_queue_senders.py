"""The review queue says who sent each video, for the "Enviado por" filter (LIVESTACK-51 item 5).

João asked to filter the queue by sender. The review UI filters the list
the orchestrator returns, so each queue item carries:

* `sender_phone`: the sender's number as digits, the key of Admin → Contatos;
* `sender_display_name`: the name the admin gave that contact, else the
  WhatsApp profile name the video arrived with.

`send_video` (orchestrator/testing/scenarios.py) posts a WhatsApp webhook
the way Evolution does; the job then waits in state `received`.
"""

from orchestrator.testing.scenarios import send_video

MARIA = "5519981234567"  # Campinas (DDD 19)
ALESSANDER = "5511987650000"  # São Paulo (DDD 11)


def queue_items(harness) -> list[dict]:
    response = harness.client.get(
        "/review/queue", params={"state": "received"}, auth=harness.reviewer_auth
    )
    assert response.status_code == 200, response.text
    return response.json()["items"]


def test_each_queue_item_names_its_sender_by_contact_name_else_whatsapp_name(harness):
    # Maria is on Admin → Contatos with the name "Maria (filmagem)"; her
    # WhatsApp profile says "Maria C.". Alessander is not named by the admin.
    harness.client.post(
        "/admin/contacts",
        json={"phone": "+55 19 98123-4567", "display_name": "Maria (filmagem)"},
        auth=harness.admin_auth,
    )
    send_video(harness, b"video from maria", sender_phone=MARIA, sender_name="Maria C.")
    send_video(harness, b"video from alessander", sender_phone=ALESSANDER, sender_name="Alessander")

    items = queue_items(harness)

    senders = sorted((item["sender_phone"], item["sender_display_name"]) for item in items)
    # The admin's name wins for Maria; Alessander's own WhatsApp name is used
    # for him, since the contact recorded on his first message got it too.
    assert senders == [(ALESSANDER, "Alessander"), (MARIA, "Maria (filmagem)")]
