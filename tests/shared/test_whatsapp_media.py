"""Tests for shared.whatsapp_media: downloading and decrypting WhatsApp files.

Background for a beginner: WhatsApp never stores files in the clear. The
sender encrypts each file with keys derived from a random 32-byte
"mediaKey", uploads the encrypted blob to WhatsApp's CDN, and sends the
mediaKey (and two SHA-256 hashes) inside the message. We get the message
through the Evolution webhook, download the blob and decrypt it.

How these tests know the decryption is right:
  * Two "known-answer" tests use values computed with the `openssl`
    command-line tool, completely outside this code base. If our key
    derivation or decryption were wrong, we could not reproduce them.
  * The other tests encrypt a generated file with the separate helper in
    shared.testing.whatsapp_media_encrypt, then decrypt it through the
    module, and break the data in various ways to check every safety check.
No real network is used: a fake transport plays the WhatsApp CDN.
"""

import hashlib
import os

import httpx
import pytest

from shared.http_transfer import partial_path_for
from shared.models import EvolutionWebhook, ReceivedAs, find_incoming_video
from shared.testing.evolution_payloads import EXAMPLE_URL, document_message_payload
from shared.testing.whatsapp_media_encrypt import encrypt_bytes
from shared.whatsapp_media import (
    MediaDownloadError,
    MediaIntegrityError,
    StreamingMediaDecryptor,
    derive_media_keys,
    download_and_decrypt,
    download_incoming_media,
)

# --- Known-answer vectors, computed with OpenSSL 3 (not with our code) ----
#   openssl kdf -keylen 112 -kdfopt digest:SHA256 -kdfopt hexkey:0001..1f \
#       -kdfopt "info:WhatsApp Document Keys" HKDF
KNOWN_MEDIA_KEY = bytes(range(32))  # 00 01 02 ... 1f
KNOWN_EXPANSION = bytes.fromhex(
    "25E9D370804DE0BB33EA5DB79453DB66B75C2BED8E162AD63DD02B6B9FBDA46B"
    "8AD2692E99ED0F877D0AB48A86FFF79094A393DAFDCA1265C99E197425DE98F1"
    "5F7D87D34714EA901F7EEFC29C99E5ACF826177AC4AAF590F67E927133B8A7F1"
    "02A9FA5C2A88D62587699F581B2E9B76"
)
#   printf 'Baja SAE pass video, enduro car 42.\n' \
#       | openssl enc -aes-256-cbc -K <cipherKey> -iv <iv>
KNOWN_PLAINTEXT = b"Baja SAE pass video, enduro car 42.\n"
KNOWN_CIPHERTEXT = bytes.fromhex(
    "c3c24fbbd48a5b552b6ebceedc6d0af5666493f3fa0e14c06904df4e8c97c8cb"
    "234500aa2cf66e37532ddfb4ed774e04"
)
#   (iv + ciphertext) | openssl dgst -sha256 -mac HMAC -macopt hexkey:<macKey>, first 10 bytes
KNOWN_MAC = bytes.fromhex("ef0ab5cdd145c9d43c32")


def test_key_derivation_matches_openssl():
    # HKDF turns the 32-byte mediaKey into 112 bytes, which are cut into
    # iv (16), cipherKey (32), macKey (32), refKey (32), in that order.
    keys = derive_media_keys(KNOWN_MEDIA_KEY, ReceivedAs.DOCUMENT)
    assert keys.iv == KNOWN_EXPANSION[0:16]
    assert keys.cipher_key == KNOWN_EXPANSION[16:48]
    assert keys.mac_key == KNOWN_EXPANSION[48:80]
    assert keys.ref_key == KNOWN_EXPANSION[80:112]


def test_decrypting_an_openssl_made_blob_gives_the_original_text():
    # The CDN blob is ciphertext followed by the 10-byte MAC. The two
    # hashes are what the webhook would carry for this file.
    blob = KNOWN_CIPHERTEXT + KNOWN_MAC
    decryptor = StreamingMediaDecryptor(
        derive_media_keys(KNOWN_MEDIA_KEY, ReceivedAs.DOCUMENT),
        file_enc_sha256=hashlib.sha256(blob).digest(),
        file_sha256=hashlib.sha256(KNOWN_PLAINTEXT).digest(),
    )
    plaintext = decryptor.feed(blob) + decryptor.finish()
    assert plaintext == KNOWN_PLAINTEXT


# --- Round trips through the separate test encryptor ----------------------


@pytest.fixture
def generated_video():
    """~1.3 MB of random bytes standing in for a video, plus its encrypted blob."""
    plaintext = os.urandom(1_300_017)  # not a multiple of 16: padding matters
    blob, encrypted = encrypt_bytes(plaintext)
    return plaintext, blob, encrypted


def fake_cdn(blob: bytes, fail_first_attempts: int = 0):
    """An httpx client whose 'network' answers every GET with `blob`.

    With fail_first_attempts=N, the first N requests fail with a simulated
    connection error. requests_seen counts the requests made.
    """
    requests_seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests_seen.append(request)
        if len(requests_seen) <= fail_first_attempts:
            raise httpx.ConnectError("simulated network failure")
        return httpx.Response(200, content=blob)

    return httpx.Client(transport=httpx.MockTransport(handler)), requests_seen


@pytest.mark.parametrize("piece_size", [1, 7, 16, 4096, 10_000_000])
def test_any_download_piece_size_decrypts_the_same(generated_video, piece_size):
    # The network delivers bytes in pieces of unpredictable size. Whether
    # pieces are 1 byte or bigger than the whole file, the decrypted result
    # must be identical. (1-byte pieces only for the first 3000 bytes of
    # data, to keep the test fast.)
    plaintext, blob, encrypted = generated_video
    if piece_size == 1:
        plaintext = plaintext[:3000]
        blob, encrypted = encrypt_bytes(plaintext)
    decryptor = StreamingMediaDecryptor(
        derive_media_keys(encrypted.media_key, ReceivedAs.DOCUMENT),
        encrypted.file_enc_sha256,
        encrypted.file_sha256,
    )
    pieces = [blob[i : i + piece_size] for i in range(0, len(blob), piece_size)]
    output = b"".join(decryptor.feed(piece) for piece in pieces) + decryptor.finish()
    assert output == plaintext


def test_download_and_decrypt_writes_the_verified_file(generated_video, tmp_path):
    plaintext, blob, encrypted = generated_video
    client, _ = fake_cdn(blob)
    destination = tmp_path / "inbox" / "job1.mp4"

    result = download_and_decrypt(
        client,
        "https://mmg.whatsapp.net/v/x.enc",
        derive_media_keys(encrypted.media_key, ReceivedAs.DOCUMENT),
        encrypted.file_enc_sha256,
        encrypted.file_sha256,
        destination,
        expected_length=len(plaintext),
    )

    assert destination.read_bytes() == plaintext
    # The job's sha256 is the plaintext hash, as lowercase hex.
    assert result.sha256 == hashlib.sha256(plaintext).hexdigest()
    assert result.size_bytes == len(plaintext)
    # The hidden temporary file was renamed away.
    assert not partial_path_for(destination).exists()


def decrypt_to(tmp_path, client, encrypted, kind=ReceivedAs.DOCUMENT, **overrides):
    """Call download_and_decrypt with the right values unless a test overrides one."""
    arguments = {
        "keys": derive_media_keys(encrypted.media_key, kind),
        "file_enc_sha256": encrypted.file_enc_sha256,
        "file_sha256": encrypted.file_sha256,
    }
    arguments.update(overrides)
    destination = tmp_path / "out.mp4"
    download_and_decrypt(
        client, "https://mmg.whatsapp.net/v/x.enc", destination=destination,
        sleep=lambda seconds: None, **arguments,
    )  # fmt: skip
    return destination


def test_changed_download_fails_the_encrypted_hash_check(generated_video, tmp_path):
    # Flip one byte of the blob (as a broken CDN or an attacker might).
    # The SHA-256 of the download no longer matches fileEncSha256.
    _, blob, encrypted = generated_video
    damaged = bytearray(blob)
    damaged[500] ^= 0xFF
    client, _ = fake_cdn(bytes(damaged))
    with pytest.raises(MediaIntegrityError) as caught:
        decrypt_to(tmp_path, client, encrypted)
    assert caught.value.check == "file_enc_sha256"
    # Nothing is left behind: no final file and no hidden partial file.
    assert not (tmp_path / "out.mp4").exists()
    assert not partial_path_for(tmp_path / "out.mp4").exists()


def test_wrong_media_key_fails_the_mac_check(generated_video, tmp_path):
    # Same blob and same encrypted hash, but keys from another mediaKey:
    # the MAC (which only the right macKey can produce) catches it.
    _, blob, encrypted = generated_video
    client, _ = fake_cdn(blob)
    other_keys = derive_media_keys(os.urandom(32), ReceivedAs.DOCUMENT)
    with pytest.raises(MediaIntegrityError) as caught:
        decrypt_to(tmp_path, client, encrypted, keys=other_keys)
    assert caught.value.check == "mac"


def test_video_messages_use_different_keys_than_documents(tmp_path):
    # A WhatsApp *video* is encrypted with "WhatsApp Video Keys". Trying
    # document keys on it must fail; video keys must work.
    blob, encrypted = encrypt_bytes(b"recompressed video" * 100, media_type="video")
    client, _ = fake_cdn(blob)
    with pytest.raises(MediaIntegrityError):
        decrypt_to(tmp_path, client, encrypted, kind=ReceivedAs.DOCUMENT)
    destination = decrypt_to(tmp_path, client, encrypted, kind=ReceivedAs.VIDEO)
    assert destination.read_bytes() == b"recompressed video" * 100


def test_wrong_plaintext_hash_fails_the_last_check(generated_video, tmp_path):
    # Everything about the download is fine, but the webhook's fileSha256
    # says a different file was sent. We must refuse to create the job.
    _, blob, encrypted = generated_video
    client, _ = fake_cdn(blob)
    with pytest.raises(MediaIntegrityError) as caught:
        decrypt_to(tmp_path, client, encrypted, file_sha256=bytes(32))
    assert caught.value.check == "file_sha256"


def test_length_different_from_file_length_is_refused(generated_video, tmp_path):
    plaintext, blob, encrypted = generated_video
    client, _ = fake_cdn(blob)
    with pytest.raises(MediaIntegrityError) as caught:
        download_and_decrypt(
            client, "https://mmg.whatsapp.net/v/x.enc",
            derive_media_keys(encrypted.media_key, ReceivedAs.DOCUMENT),
            encrypted.file_enc_sha256, encrypted.file_sha256, tmp_path / "out.mp4",
            expected_length=len(plaintext) + 1,
        )  # fmt: skip
    assert caught.value.check == "file_length"


def test_network_failure_restarts_the_download_from_the_beginning(generated_video, tmp_path):
    # The first request fails (simulated). The second starts again from
    # byte 0 (no Range header), because AES-CBC decryption cannot just
    # pick up in the middle, and the result is still correct.
    plaintext, blob, encrypted = generated_video
    client, requests_seen = fake_cdn(blob, fail_first_attempts=1)
    destination = decrypt_to(tmp_path, client, encrypted)
    assert destination.read_bytes() == plaintext
    assert len(requests_seen) == 2
    assert "range" not in requests_seen[1].headers


def test_gives_up_after_max_attempts(generated_video, tmp_path):
    _, blob, encrypted = generated_video
    client, _ = fake_cdn(blob, fail_first_attempts=99)
    with pytest.raises(MediaDownloadError):
        decrypt_to(tmp_path, client, encrypted)


# --- The whole intake path, from webhook JSON to a decrypted file -----------


def test_download_incoming_media_from_a_parsed_webhook(tmp_path):
    # Build the webhook JSON for an encrypted file, parse it with the same
    # model the orchestrator uses, and let download_incoming_media do the
    # rest: pick the URL, derive keys, download, decrypt, verify.
    plaintext = os.urandom(70_000)
    blob, encrypted = encrypt_bytes(plaintext)
    payload = document_message_payload(encrypted)
    incoming = find_incoming_video(EvolutionWebhook.model_validate(payload).data.message)

    client, requests_seen = fake_cdn(blob)
    result = download_incoming_media(client, incoming, tmp_path / "raw.mp4")

    assert result.path.read_bytes() == plaintext
    # It fetched the URL from the webhook, with Baileys' Origin header.
    assert str(requests_seen[0].url) == EXAMPLE_URL
    assert requests_seen[0].headers["origin"] == "https://web.whatsapp.com"


def test_download_incoming_media_refuses_a_message_without_keys(tmp_path):
    payload = document_message_payload()
    del payload["data"]["message"]["documentMessage"]["mediaKey"]
    incoming = find_incoming_video(EvolutionWebhook.model_validate(payload).data.message)
    client, requests_seen = fake_cdn(b"")
    with pytest.raises(MediaDownloadError, match="media_key"):
        download_incoming_media(client, incoming, tmp_path / "raw.mp4")
    # It gave up before touching the network.
    assert requests_seen == []
