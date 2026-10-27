"""Tests for reading Evolution API webhooks (shared.models.evolution).

The example payloads come from shared.testing.evolution_payloads, the same
builder the fake webhook poster uses, so the dev tool and the tests agree.
"""

import base64

import pytest
from pydantic import ValidationError

from shared.models import EvolutionWebhook, ReceivedAs, find_incoming_video
from shared.models.evolution import decode_whatsapp_binary
from shared.testing.evolution_payloads import (
    EXAMPLE_DIRECT_PATH,
    EXAMPLE_URL,
    document_message_payload,
    text_message_payload,
    video_message_payload,
)
from shared.testing.whatsapp_media_encrypt import encrypt_bytes


def parse(payload: dict) -> EvolutionWebhook:
    return EvolutionWebhook.model_validate(payload)


def test_document_message_carries_everything_needed_to_decrypt():
    # Encrypt some bytes so the payload holds a real key and real hashes.
    _, encrypted = encrypt_bytes(b"video" * 1000)
    payload = document_message_payload(encrypted, file_name="pass.mp4", message_id="ABC")
    webhook = parse(payload)
    incoming = find_incoming_video(webhook.data.message)

    # A video sent as a *document* keeps full quality: received_as DOCUMENT.
    assert incoming is not None
    assert incoming.received_as == ReceivedAs.DOCUMENT
    media = incoming.media
    # Where to download the encrypted file...
    assert media.url == EXAMPLE_URL
    assert media.direct_path == EXAMPLE_DIRECT_PATH
    # ...and the binary fields, decoded from base64 back to the exact bytes.
    assert media.media_key == encrypted.media_key
    assert media.file_sha256 == encrypted.file_sha256
    assert media.file_enc_sha256 == encrypted.file_enc_sha256
    # WhatsApp sends sizes as text ("5000"); the model turns it into a number.
    assert media.file_length == 5000
    assert media.file_name == "pass.mp4"
    assert media.mimetype == "video/mp4"
    assert incoming.missing_decryption_fields() == []
    # The message id is what duplicate detection uses.
    assert webhook.data.key.id == "ABC"
    assert webhook.data.push_name == "Voluntário Teste"


def test_there_is_no_media_url_field_any_more():
    # Design §15: mediaUrl (Evolution's S3 option) is not used. Even if a
    # payload has one, the model does not expose it as a field we read.
    payload = document_message_payload()
    payload["data"]["message"]["mediaUrl"] = "http://minio/x.mp4"
    message = parse(payload).data.message
    assert "media_url" not in type(message).model_fields


def test_video_message_is_accepted_but_marked_as_video():
    # Design §3: a recompressed WhatsApp *video* is accepted, flagged low_res.
    incoming = find_incoming_video(parse(video_message_payload()).data.message)
    assert incoming is not None
    assert incoming.received_as == ReceivedAs.VIDEO


def test_document_that_is_not_a_video_is_ignored():
    # Someone sends a PDF as a document: not ours to process.
    payload = document_message_payload(mimetype="application/pdf")
    assert find_incoming_video(parse(payload).data.message) is None


def test_text_message_is_ignored():
    assert find_incoming_video(parse(text_message_payload()).data.message) is None


def test_document_with_caption_is_unwrapped():
    # When the sender types a caption, WhatsApp nests the document one
    # level deeper, under documentWithCaptionMessage.message. We must still
    # find it.
    payload = document_message_payload()
    message = payload["data"]["message"]
    document = message.pop("documentMessage")
    message["documentWithCaptionMessage"] = {"message": {"documentMessage": document}}

    incoming = find_incoming_video(parse(payload).data.message)
    assert incoming is not None
    assert incoming.received_as == ReceivedAs.DOCUMENT


def test_missing_fields_are_listed_by_name():
    # Without a key and without any URL the file cannot be fetched; intake
    # uses this list to log why it ignored the message.
    payload = document_message_payload(url=None, direct_path=None)
    del payload["data"]["message"]["documentMessage"]["mediaKey"]
    incoming = find_incoming_video(parse(payload).data.message)
    assert incoming.missing_decryption_fields() == ["media_key", "url/direct_path"]


def test_unknown_evolution_fields_are_tolerated():
    # Evolution sends many fields we do not model. Unlike our own API,
    # this model must accept them, or every Evolution upgrade could break
    # intake.
    payload = document_message_payload()
    payload["some_future_field"] = {"x": 1}
    payload["data"]["contextInfo"] = {}
    assert parse(payload).event == "messages.upsert"


# --- Binary fields can arrive in three JSON shapes -------------------------

KEY = bytes([12, 200, 0, 255])


@pytest.mark.parametrize(
    "json_value",
    [
        base64.b64encode(KEY).decode(),  # "DMgA/w=="
        {"type": "Buffer", "data": [12, 200, 0, 255]},  # Node.js Buffer.toJSON()
        {"0": 12, "1": 200, "2": 0, "3": 255},  # index-keyed object
    ],
)
def test_binary_fields_accept_every_shape_evolution_uses(json_value):
    # Whatever shape the bytes come in, the result is the same bytes.
    assert decode_whatsapp_binary(json_value) == KEY


def test_index_keyed_object_is_ordered_by_number_not_text():
    # Sorting "10" before "2" (as text) would scramble the bytes.
    value = {str(index): index for index in range(12)}
    assert decode_whatsapp_binary(value) == bytes(range(12))


def test_garbage_in_a_binary_field_is_a_validation_error():
    payload = document_message_payload()
    payload["data"]["message"]["documentMessage"]["mediaKey"] = "not base64!!"
    with pytest.raises(ValidationError):
        parse(payload)


def test_binary_fields_round_trip_through_json():
    # Dumping the parsed webhook to JSON writes the bytes as base64, and
    # parsing that JSON again gives back the same bytes.
    _, encrypted = encrypt_bytes(b"x" * 100)
    webhook = parse(document_message_payload(encrypted))
    again = EvolutionWebhook.model_validate_json(webhook.model_dump_json(by_alias=True))
    assert again.data.message.document_message.media_key == encrypted.media_key
