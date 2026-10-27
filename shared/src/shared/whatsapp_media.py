"""Download and decrypt WhatsApp media while streaming (design §15).

WhatsApp stores every sent file encrypted on its CDN. The webhook tells us
where (url / directPath) and gives the 32-byte `mediaKey`. From that key:

  1. HKDF-SHA256(mediaKey, info="WhatsApp Document Keys", 112 bytes)
     → iv (16) | cipherKey (32) | macKey (32) | refKey (32, unused here).
     Videos sent as *video* (our low_res case) use "WhatsApp Video Keys".
  2. The download is  AES-256-CBC(cipherKey, iv, PKCS7-padded file)
     followed by a 10-byte MAC = first 10 bytes of HMAC-SHA256(macKey, iv + ciphertext).
  3. fileEncSha256 = SHA-256 of the whole download (ciphertext + MAC);
     fileSha256    = SHA-256 of the decrypted file.

We check all three (encrypted hash, MAC, plaintext hash) and only then
rename the file into place, so a file without the dot-prefix is always a
complete, verified raw.

Reference implementation: Baileys (github.com/WhiskeySockets/Baileys),
src/Utils/messages-media.ts — `getMediaKeys` (HKDF split),
`downloadContentFromMessage` (URL choice and streaming decrypt) and
`encryptedStream` (how the sender builds the MAC and both hashes). Unlike
Baileys' downloader, we also verify the MAC and both hashes.
"""

import hashlib
import hmac
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import httpx
from cryptography.hazmat.primitives import hashes, padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from shared.http_transfer import partial_path_for
from shared.models.enums import ReceivedAs
from shared.models.evolution import IncomingMedia

# HKDF "info" text per kind of WhatsApp message (Baileys: MEDIA_HKDF_KEY_MAPPING).
HKDF_INFO_BY_KIND: dict[ReceivedAs, bytes] = {
    ReceivedAs.DOCUMENT: b"WhatsApp Document Keys",
    ReceivedAs.VIDEO: b"WhatsApp Video Keys",
}
EXPANDED_KEY_LENGTH = 112
MAC_LENGTH = 10
AES_BLOCK_BITS = 128

# Baileys' DEF_HOST. A webhook `url` is only trusted if it starts with this;
# anything else is rebuilt from directPath, so a forged webhook cannot make
# the orchestrator fetch arbitrary URLs. Dev tools point it elsewhere via
# BVP_WHATSAPP_MEDIA_BASE_URL.
DEFAULT_MEDIA_BASE_URL = "https://mmg.whatsapp.net"
# Baileys sends this Origin header on media downloads.
WHATSAPP_ORIGIN = "https://web.whatsapp.com"


class MediaIntegrityError(Exception):
    """The download does not match what the webhook promised. Never use the file."""

    def __init__(self, check: str, message: str) -> None:
        # Which check failed: "file_enc_sha256", "mac", "padding",
        # "file_sha256" or "file_length".
        self.check = check
        super().__init__(f"{check}: {message}")


class MediaDownloadError(Exception):
    pass


@dataclass(frozen=True)
class MediaKeys:
    iv: bytes
    cipher_key: bytes
    mac_key: bytes
    ref_key: bytes


@dataclass(frozen=True)
class DecryptedMedia:
    path: Path
    size_bytes: int
    # Lowercase hex, the same format as every other hash in the API.
    sha256: str


def derive_media_keys(media_key: bytes, kind: ReceivedAs) -> MediaKeys:
    """Expand the 32-byte mediaKey into the IV and keys (step 1 above)."""
    if len(media_key) != 32:
        raise ValueError(f"mediaKey must be 32 bytes, got {len(media_key)}")
    # No salt: HKDF then uses a block of zero bytes, as Baileys does.
    expanded = HKDF(
        algorithm=hashes.SHA256(),
        length=EXPANDED_KEY_LENGTH,
        salt=None,
        info=HKDF_INFO_BY_KIND[kind],
    ).derive(media_key)
    return MediaKeys(
        iv=expanded[0:16],
        cipher_key=expanded[16:48],
        mac_key=expanded[48:80],
        ref_key=expanded[80:112],
    )


def media_download_url(
    url: str | None, direct_path: str | None, base_url: str = DEFAULT_MEDIA_BASE_URL
) -> str:
    """Pick the CDN URL to fetch: the webhook's url if trusted, else base + directPath."""
    if url is not None and url.startswith(base_url.rstrip("/") + "/"):
        return url
    if direct_path is not None and direct_path.startswith("/"):
        return base_url.rstrip("/") + direct_path
    raise MediaDownloadError("no trusted url and no directPath in the message")


class StreamingMediaDecryptor:
    """Decrypts a download fed to it in pieces of any size (steps 2 and 3).

    The last 10 bytes of the download are the MAC, not ciphertext, but we
    only know which bytes are the last ones when the download ends. So the
    newest 10 bytes are always held back, and everything before them is
    decrypted straight away.
    """

    def __init__(self, keys: MediaKeys, file_enc_sha256: bytes, file_sha256: bytes) -> None:
        self._expected_encrypted_sha256 = file_enc_sha256
        self._expected_plain_sha256 = file_sha256
        self._decryptor = Cipher(algorithms.AES(keys.cipher_key), modes.CBC(keys.iv)).decryptor()
        self._unpadder = padding.PKCS7(AES_BLOCK_BITS).unpadder()
        # The MAC covers the IV followed by all the ciphertext.
        self._mac = hmac.new(keys.mac_key, keys.iv, hashlib.sha256)
        self._encrypted_sha256 = hashlib.sha256()
        self._plain_sha256 = hashlib.sha256()
        self._held_back = b""
        self.plaintext_bytes = 0

    def feed(self, downloaded: bytes) -> bytes:
        """Take the next downloaded bytes; return whatever plaintext is ready."""
        self._encrypted_sha256.update(downloaded)
        data = self._held_back + downloaded
        # With fewer than 10 bytes, data[:-10] is empty and everything is held.
        ciphertext, self._held_back = data[:-MAC_LENGTH], data[-MAC_LENGTH:]
        self._mac.update(ciphertext)
        return self._count(self._unpadder.update(self._decryptor.update(ciphertext)))

    def finish(self) -> bytes:
        """Run every check and return the last plaintext bytes.

        Order matters: hashes and MAC are checked before trusting the
        padding, so tampered data fails with a clear reason.
        """
        if self._encrypted_sha256.digest() != self._expected_encrypted_sha256:
            raise MediaIntegrityError("file_enc_sha256", "download differs from fileEncSha256")
        expected_mac = self._mac.digest()[:MAC_LENGTH]
        if len(self._held_back) != MAC_LENGTH or not hmac.compare_digest(
            self._held_back, expected_mac
        ):
            raise MediaIntegrityError("mac", "MAC does not match; wrong mediaKey or corrupt file")
        try:
            last = self._unpadder.update(self._decryptor.finalize()) + self._unpadder.finalize()
        except ValueError as bad_padding:
            raise MediaIntegrityError("padding", str(bad_padding)) from bad_padding
        last = self._count(last)
        if self._plain_sha256.digest() != self._expected_plain_sha256:
            raise MediaIntegrityError("file_sha256", "decrypted file differs from fileSha256")
        return last

    def _count(self, plaintext: bytes) -> bytes:
        self._plain_sha256.update(plaintext)
        self.plaintext_bytes += len(plaintext)
        return plaintext


def download_and_decrypt(
    client: httpx.Client,
    download_url: str,
    keys: MediaKeys,
    file_enc_sha256: bytes,
    file_sha256: bytes,
    destination: Path,
    *,
    expected_length: int | None = None,
    max_attempts: int = 3,
    retry_delay_s: float = 2.0,
    sleep: Callable[[float], None] = time.sleep,
) -> DecryptedMedia:
    """Stream the encrypted file into `destination`, decrypted and verified.

    Plaintext goes to a hidden ".<name>.part" file first. Network errors
    and 5xx answers restart the download from the beginning (CBC decryption
    cannot simply resume mid-file); a failed integrity check never retries.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial_path = partial_path_for(destination)
    attempts = 0
    while True:
        attempts += 1
        try:
            size = _download_once(
                client, download_url, keys, file_enc_sha256, file_sha256, partial_path
            )
            break
        except MediaIntegrityError:
            partial_path.unlink(missing_ok=True)
            raise
        except (httpx.TransportError, MediaDownloadError) as error:
            partial_path.unlink(missing_ok=True)
            if attempts >= max_attempts:
                raise MediaDownloadError(f"giving up after {attempts} attempts: {error}") from error
            sleep(retry_delay_s)

    if expected_length is not None and size != expected_length:
        partial_path.unlink()
        raise MediaIntegrityError(
            "file_length", f"got {size} bytes, fileLength says {expected_length}"
        )
    partial_path.replace(destination)
    return DecryptedMedia(destination, size, file_sha256.hex())


def _download_once(
    client: httpx.Client,
    download_url: str,
    keys: MediaKeys,
    file_enc_sha256: bytes,
    file_sha256: bytes,
    partial_path: Path,
) -> int:
    decryptor = StreamingMediaDecryptor(keys, file_enc_sha256, file_sha256)
    headers = {"Origin": WHATSAPP_ORIGIN, "Accept-Encoding": "identity"}
    with client.stream("GET", download_url, headers=headers) as response:
        if response.status_code != 200:
            # 5xx and 404s from an expiring CDN link are worth one more try.
            raise MediaDownloadError(f"CDN answered HTTP {response.status_code}")
        with partial_path.open("wb") as partial_file:
            for downloaded in response.iter_bytes():
                partial_file.write(decryptor.feed(downloaded))
            partial_file.write(decryptor.finish())
    return decryptor.plaintext_bytes


def download_incoming_media(
    client: httpx.Client,
    incoming: IncomingMedia,
    destination: Path,
    base_url: str = DEFAULT_MEDIA_BASE_URL,
) -> DecryptedMedia:
    """Everything intake needs in one call: URL choice, keys, download, checks."""
    missing = incoming.missing_decryption_fields()
    if missing:
        raise MediaDownloadError(f"webhook lacks {', '.join(missing)}")
    media = incoming.media
    return download_and_decrypt(
        client,
        media_download_url(media.url, media.direct_path, base_url),
        derive_media_keys(media.media_key, incoming.received_as),
        media.file_enc_sha256,
        media.file_sha256,
        destination,
        expected_length=media.file_length,
    )
