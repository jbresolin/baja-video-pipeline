"""Which of the sender's messages a video's page lists (LIVESTACK-49).

Filmmakers write what the reviewer needs next to the video: "carro 42,
segunda passagem" before or after sending it, or a reply to the video
saying "errei, é a 3". The bot keeps those messages and the job page shows
them in a section "Mensagens de <remetente>" (GET /jobs/{id}/sender-messages):

* the video's own caption, first;
* replies that quote this video, wherever they fall in time;
* "after": messages received after this video and before the sender's next;
* "before": messages received after the sender's previous video and before
  this one.

Every test posts real-looking Evolution 2.3.7 webhooks (the shared payload
helpers) and moves the test clock forward a minute between messages, so
the order in time is the order written in the test.
"""

from orchestrator.testing.scenarios import (
    post_webhook,
    receive_job,
    sender_messages,
)
from shared.models import WorkerRole
from shared.testing.evolution_payloads import (
    as_evolution_2_3_7_lid_payload,
    media_note_payload,
    text_message_payload,
)

MARIA = "5511987650001"
JOAO = "5511987650002"


def say(harness, text, sender=MARIA, *, quoting=None, message_id=None):
    """The filmmaker sends a text one minute after whatever happened last."""
    harness.clock.advance(60)
    payload = text_message_payload(text, sender, message_id=message_id, quoted_message_id=quoting)
    answer = post_webhook(harness, payload)
    return answer


def send(harness, sender=MARIA, caption=None):
    """The filmmaker sends a video, one minute later. Returns (job id, its message id)."""
    harness.clock.advance(60)
    job_id = receive_job(harness, sender_phone=sender, caption=caption)
    return job_id, harness.job(job_id)["source"]["message_id"]


def listed(harness, job_id):
    """The section as (role, text) pairs, in the order the page shows them."""
    return [(item["role"], item["text"]) for item in sender_messages(harness, job_id)]


def test_a_text_is_kept_without_making_a_job_or_a_reply(harness):
    # A text from a filmmaker: 200 (Evolution must not retry), no job, no
    # bot answer, but kept for the job page.
    answer = say(harness, "bom dia, vou mandar os vídeos do carro 42")
    assert answer["ignored"] is True and answer["message_stored"] is True
    assert harness.evolution.sent == [] and harness.evolution.reactions == []
    queue = harness.client.get("/review/queue?state=received", auth=harness.reviewer_auth)
    assert queue.json()["items"] == []


def test_a_text_after_the_video_is_in_its_section_and_not_after_the_next_video(harness):
    first, _ = send(harness)
    say(harness, "carro 42, segunda passagem")
    second, _ = send(harness)
    say(harness, "esse é o 17")

    # The first video lists the text sent after it, and stops at the second video.
    assert listed(harness, first) == [("after", "carro 42, segunda passagem")]
    # The second video lists its own "after" text, and the first text again,
    # under "before" (sent between the two videos).
    assert listed(harness, second) == [
        ("after", "esse é o 17"),
        ("before", "carro 42, segunda passagem"),
    ]


def test_a_reply_quoting_the_first_video_is_listed_there_even_after_the_second(harness):
    first, first_message_id = send(harness)
    second, _ = send(harness)
    # Sent after the second video, but as a reply ("swipe to reply") to the first.
    say(harness, "errei, é a 3", quoting=first_message_id)

    assert listed(harness, first) == [("reply", "errei, é a 3")]
    # It belongs to the first video only: the second does not show it, even
    # though it arrived after it.
    assert listed(harness, second) == []


def test_before_this_video_lists_what_came_after_the_previous_one_oldest_first(harness):
    say(harness, "oi, sou a Maria")  # before any video: the first video's "before"
    first, _ = send(harness)
    say(harness, "agora o carro 17")
    say(harness, "primeira passagem dele")
    second, _ = send(harness)

    assert listed(harness, second) == [
        ("before", "agora o carro 17"),
        ("before", "primeira passagem dele"),
    ]
    # The first video has no previous one: everything earlier counts as before.
    assert listed(harness, first) == [
        ("after", "agora o carro 17"),
        ("after", "primeira passagem dele"),
        ("before", "oi, sou a Maria"),
    ]


def test_the_caption_comes_first_then_replies_then_after_then_before(harness):
    say(harness, "o próximo é o 42")
    video, message_id = send(harness, caption="carro 42 p2")
    say(harness, "foi na reta")
    say(harness, "na verdade é a passagem 3", quoting=message_id)

    # The caption is also on the job itself (IntakeSource.caption).
    assert harness.job(video)["source"]["caption"] == "carro 42 p2"
    assert listed(harness, video) == [
        ("caption", "carro 42 p2"),
        ("reply", "na verdade é a passagem 3"),
        ("after", "foi na reta"),
        ("before", "o próximo é o 42"),
    ]


def test_two_senders_writing_at_the_same_time_never_mix(harness):
    # Maria and João send videos and texts interleaved, minute by minute.
    maria_video, _ = send(harness, MARIA)
    joao_video, joao_message_id = send(harness, JOAO)
    say(harness, "Maria: carro 42", MARIA)
    say(harness, "João: carro 7", JOAO)
    # João's reply quotes his own video; Maria's next video does not end
    # João's "after" span, only João's own next video would.
    maria_second, _ = send(harness, MARIA)
    say(harness, "João: segunda passagem", JOAO, quoting=joao_message_id)
    say(harness, "João: obrigado", JOAO)

    assert listed(harness, maria_video) == [("after", "Maria: carro 42")]
    assert listed(harness, maria_second) == [("before", "Maria: carro 42")]
    assert listed(harness, joao_video) == [
        ("reply", "João: segunda passagem"),
        ("after", "João: carro 7"),
        ("after", "João: obrigado"),
    ]


def test_media_without_text_is_listed_by_kind_and_an_image_keeps_its_caption(harness):
    video, _ = send(harness)
    harness.clock.advance(60)
    post_webhook(harness, media_note_payload("audioMessage", MARIA))
    harness.clock.advance(60)
    post_webhook(harness, media_note_payload("imageMessage", MARIA, caption="o adesivo do 42"))
    harness.clock.advance(60)
    post_webhook(harness, media_note_payload("stickerMessage", MARIA))

    items = sender_messages(harness, video)
    # Nothing is downloaded: an audio is just "audio" (the page says "(áudio)").
    assert [(item["kind"], item["text"]) for item in items] == [
        ("audio", None),
        ("image", "o adesivo do 42"),
        ("sticker", None),
    ]


def test_a_reaction_is_not_kept(harness):
    # A filmmaker's 👍 on a message is not something to read on the job page.
    video, message_id = send(harness)
    harness.clock.advance(60)
    reaction = text_message_payload("x", MARIA)
    reaction["data"]["message"] = {"reactionMessage": {"key": {"id": message_id}, "text": "👍"}}
    reaction["data"]["messageType"] = "reactionMessage"
    answer = post_webhook(harness, reaction)
    assert answer["message_stored"] is False
    assert listed(harness, video) == []


def test_an_lid_sender_with_long_timestamps_is_matched_to_their_video(harness):
    # Evolution 2.3.7 as seen in production: the sender as "...@lid" with
    # the phone in remoteJidAlt, and WhatsApp's 64-bit numbers as Long objects.
    video = receive_job(harness, sender_phone=MARIA, reshape=as_evolution_2_3_7_lid_payload)
    harness.clock.advance(60)
    text = text_message_payload("carro 42", MARIA)
    text["data"]["messageTimestamp"] = {"low": 1793844688, "high": 0, "unsigned": True}
    post_webhook(harness, text, reshape=as_evolution_2_3_7_lid_payload)

    assert listed(harness, video) == [("after", "carro 42")]


def test_a_repeated_webhook_is_kept_once(harness):
    video, _ = send(harness)
    harness.clock.advance(60)
    payload = text_message_payload("carro 42", MARIA, message_id="3EB0AAAA0001")
    post_webhook(harness, payload)
    again = post_webhook(harness, payload)
    assert again["duplicate"] is True
    assert listed(harness, video) == [("after", "carro 42")]


def test_the_queue_and_the_car_panel_count_each_videos_messages(harness):
    # The queue row's speech bubble shows how many messages the page lists;
    # both lists also say when each video was received.
    say(harness, "vem aí o 42")
    video, message_id = send(harness, caption="42")
    say(harness, "foi a segunda", quoting=message_id)
    other, _ = send(harness, JOAO)

    queue = harness.client.get("/review/queue?state=received", auth=harness.reviewer_auth)
    by_id = {item["id"]: item for item in queue.json()["items"]}
    # Caption, the reply and the earlier text.
    assert by_id[video]["sender_message_count"] == 3
    assert by_id[other]["sender_message_count"] == 0
    assert by_id[video]["received_at"] == harness.job(video)["source"]["received_at"]


def test_only_reviewers_read_the_messages_and_an_unknown_job_is_404(harness):
    video, _ = send(harness)
    worker = harness.enroll(WorkerRole.WORKER, "pc")
    # A worker may read the job but not people's messages.
    as_worker = harness.client.get(f"/jobs/{video}/sender-messages", headers=worker.headers)
    assert as_worker.status_code == 401
    missing = harness.client.get("/jobs/nope/sender-messages", auth=harness.reviewer_auth)
    assert missing.status_code == 404
