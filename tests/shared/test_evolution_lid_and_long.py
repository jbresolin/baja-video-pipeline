"""Two quirks of real Evolution 2.3.7 webhooks, first seen on 2026-11-04.

Background for a beginner: our first real video was rejected with HTTP 400.
The webhook had two things our tests had never shown:

1. Big numbers as objects. WhatsApp sends sizes and timestamps as 64-bit
   integers. JavaScript (which Evolution is written in) cannot hold every
   64-bit value in a normal number, so the library it uses keeps them as a
   "Long" object made of two 32-bit halves:
       {"low": 14007026, "high": 0, "unsigned": true}
   The real value is high * 2**32 + low. Our model expected a plain number.

2. Hidden phone numbers. WhatsApp now often identifies a private sender by
   an anonymous id ending in "@lid" instead of their phone number. The phone
   number then sits in a second field, "remoteJidAlt". The bot must reply to
   the phone number, because Evolution's sendText needs one.
"""

import pytest
from pydantic import ValidationError

from shared.models import EvolutionWebhook
from shared.models.evolution import decode_whatsapp_integer
from shared.testing.evolution_payloads import (
    as_evolution_2_3_7_lid_payload,
    document_message_payload,
)


@pytest.mark.parametrize(
    ("json_value", "expected"),
    [
        (14007026, 14007026),  # a plain number, the classic shape
        ("14007026", 14007026),  # a string of digits
        ({"low": 14007026, "high": 0, "unsigned": True}, 14007026),  # the Long object we saw
        # high counts in units of 2**32: 1 * 4294967296 + 5 = 4294967301
        ({"low": 5, "high": 1, "unsigned": True}, 4294967301),
        # In a SIGNED Long the top bit means "negative" (two's complement),
        # so all-ones is -1. Unsigned, the same bits are the largest value.
        ({"low": -1, "high": -1, "unsigned": False}, -1),
        ({"low": -1, "high": -1, "unsigned": True}, 2**64 - 1),
        (None, None),
    ],
)
def test_64_bit_numbers_are_read_in_every_shape(json_value, expected):
    assert decode_whatsapp_integer(json_value) == expected


def test_a_number_field_with_garbage_is_still_a_validation_error():
    # Being lenient about SHAPES must not mean accepting nonsense.
    payload = document_message_payload()
    payload["data"]["message"]["documentMessage"]["fileLength"] = {"nonsense": 1}
    with pytest.raises(ValidationError):
        EvolutionWebhook.model_validate(payload)


def test_the_real_2_3_7_shape_is_accepted():
    # The exact shape that came back as HTTP 400 in production: LID sender,
    # phone in remoteJidAlt, empty participant, Long fileLength.
    payload = as_evolution_2_3_7_lid_payload(document_message_payload(sender_phone="555499982643"))
    webhook = EvolutionWebhook.model_validate(payload)
    document = webhook.data.message.document_message
    # The Long object became a normal integer...
    assert isinstance(document.file_length, int)
    # ...with the same value the builder put in before reshaping it.
    original = document_message_payload()["data"]["message"]["documentMessage"]["fileLength"]
    assert document.file_length == int(original)


def test_a_lid_sender_is_answered_on_their_phone_number():
    payload = as_evolution_2_3_7_lid_payload(document_message_payload(sender_phone="555499982643"))
    key = EvolutionWebhook.model_validate(payload).data.key
    # The raw id is the anonymous LID...
    assert key.remote_jid == "53880532508775@lid"
    # ...but sender_jid, which replies and contacts use, is the phone.
    assert key.sender_jid == "555499982643@s.whatsapp.net"


def test_a_classic_sender_is_unchanged():
    key = EvolutionWebhook.model_validate(
        document_message_payload(sender_phone="5511912345678")
    ).data.key
    assert key.sender_jid == key.remote_jid == "5511912345678@s.whatsapp.net"


def test_a_lid_sender_without_the_phone_falls_back_to_the_lid():
    # If Evolution ever omits remoteJidAlt we still have SOMEONE to answer;
    # better a reply that may not arrive than a crash.
    payload = as_evolution_2_3_7_lid_payload(document_message_payload())
    del payload["data"]["key"]["remoteJidAlt"]
    key = EvolutionWebhook.model_validate(payload).data.key
    assert key.sender_jid == "53880532508775@lid"


def test_an_empty_participant_is_not_mistaken_for_a_group():
    # Evolution sends participant="" in private chats. An empty string must
    # not trigger the "videos by DM only" rule.
    payload = as_evolution_2_3_7_lid_payload(document_message_payload())
    assert EvolutionWebhook.model_validate(payload).data.key.is_group_or_broadcast is False
