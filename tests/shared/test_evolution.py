"""Tests for reading Evolution API webhooks (shared.models.evolution).

The example payloads come from shared.testing.evolution_payloads, the same
builder the fake webhook poster uses, so the dev tool and the tests agree.
"""

from shared.models import EvolutionWebhook, ReceivedAs, find_incoming_video
from shared.testing.evolution_payloads import (
    document_message_payload,
    text_message_payload,
    video_message_payload,
)


def parse(payload: dict) -> EvolutionWebhook:
    return EvolutionWebhook.model_validate(payload)


def test_document_message_is_found_with_its_details():
    payload = document_message_payload(
        "http://files/raw.mp4", file_name="pass.mp4", file_length=1234, message_id="ABC"
    )
    webhook = parse(payload)
    incoming = find_incoming_video(webhook.data.message)

    # A video sent as a *document* keeps full quality: received_as DOCUMENT.
    assert incoming is not None
    assert incoming.received_as == ReceivedAs.DOCUMENT
    assert incoming.media.file_name == "pass.mp4"
    # WhatsApp sends sizes as text ("1234"); the model turns it into a number.
    assert incoming.media.file_length == 1234
    # mediaUrl is where the orchestrator downloads the raw from.
    assert incoming.media_url == "http://files/raw.mp4"
    # The message id is what duplicate detection uses.
    assert webhook.data.key.id == "ABC"
    assert webhook.data.push_name == "Voluntário Teste"


def test_video_message_is_accepted_but_marked_as_video():
    # Design §3: a recompressed WhatsApp *video* is accepted, flagged low_res.
    incoming = find_incoming_video(parse(video_message_payload("http://files/v.mp4")).data.message)
    assert incoming is not None
    assert incoming.received_as == ReceivedAs.VIDEO


def test_document_that_is_not_a_video_is_ignored():
    # Someone sends a PDF as a document: not ours to process.
    payload = document_message_payload("http://files/a.pdf", mimetype="application/pdf")
    assert find_incoming_video(parse(payload).data.message) is None


def test_text_message_is_ignored():
    assert find_incoming_video(parse(text_message_payload()).data.message) is None


def test_document_with_caption_is_unwrapped():
    # When the sender types a caption, WhatsApp nests the document one
    # level deeper, under documentWithCaptionMessage.message. We must still
    # find it.
    payload = document_message_payload("http://files/raw.mp4")
    message = payload["data"]["message"]
    document = message.pop("documentMessage")
    message["documentWithCaptionMessage"] = {"message": {"documentMessage": document}}

    incoming = find_incoming_video(parse(payload).data.message)
    assert incoming is not None
    assert incoming.received_as == ReceivedAs.DOCUMENT


def test_unknown_evolution_fields_are_tolerated():
    # Evolution sends many fields we do not model. Unlike our own API,
    # this model must accept them, or every Evolution upgrade could break
    # intake.
    payload = document_message_payload("http://files/raw.mp4")
    payload["some_future_field"] = {"x": 1}
    payload["data"]["contextInfo"] = {}
    assert parse(payload).event == "messages.upsert"
