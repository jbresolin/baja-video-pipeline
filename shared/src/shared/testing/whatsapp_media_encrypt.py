"""Encrypt a file the way a WhatsApp sender does, for tests and the fake webhook.

The mirror image of shared.whatsapp_media (see Baileys `encryptedStream`
in src/Utils/messages-media.ts). It deliberately does NOT reuse that
module's key derivation, so a mistake there cannot be cancelled out by
the same mistake here.
"""

import base64
import hashlib
import hmac
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from cryptography.hazmat.primitives import hashes, padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

INFO_BY_MEDIA_TYPE = {"document": b"WhatsApp Document Keys", "video": b"WhatsApp Video Keys"}
_READ_CHUNK_BYTES = 1024 * 1024


@dataclass(frozen=True)
class EncryptedMedia:
    """What a webhook carries about one encrypted file (all raw bytes)."""

    media_key: bytes
    file_sha256: bytes
    file_enc_sha256: bytes
    file_length: int

    def as_webhook_fields(self) -> dict[str, str]:
        """The camelCase base64 fields Evolution puts in documentMessage/videoMessage."""
        return {
            "mediaKey": base64.b64encode(self.media_key).decode(),
            "fileSha256": base64.b64encode(self.file_sha256).decode(),
            "fileEncSha256": base64.b64encode(self.file_enc_sha256).decode(),
            "fileLength": str(self.file_length),
        }


def encrypt_file(
    source: Path,
    encrypted_destination: Path,
    media_type: str = "document",
    media_key: bytes | None = None,
) -> EncryptedMedia:
    """Write the CDN blob (ciphertext + 10-byte MAC) for `source`, streaming."""
    media_key = media_key or os.urandom(32)
    expanded = HKDF(
        algorithm=hashes.SHA256(), length=112, salt=None, info=INFO_BY_MEDIA_TYPE[media_type]
    ).derive(media_key)
    iv, cipher_key, mac_key = expanded[:16], expanded[16:48], expanded[48:80]

    encryptor = Cipher(algorithms.AES(cipher_key), modes.CBC(iv)).encryptor()
    padder = padding.PKCS7(128).padder()
    mac = hmac.new(mac_key, iv, hashlib.sha256)
    plain_sha256 = hashlib.sha256()
    encrypted_sha256 = hashlib.sha256()
    length = 0

    def write_ciphertext(ciphertext: bytes) -> None:
        mac.update(ciphertext)
        encrypted_sha256.update(ciphertext)
        out.write(ciphertext)

    with source.open("rb") as plain, encrypted_destination.open("wb") as out:
        while chunk := plain.read(_READ_CHUNK_BYTES):
            plain_sha256.update(chunk)
            length += len(chunk)
            write_ciphertext(encryptor.update(padder.update(chunk)))
        write_ciphertext(encryptor.update(padder.finalize()) + encryptor.finalize())
        # The MAC is appended after the ciphertext and is part of fileEncSha256.
        mac_tail = mac.digest()[:10]
        encrypted_sha256.update(mac_tail)
        out.write(mac_tail)

    return EncryptedMedia(media_key, plain_sha256.digest(), encrypted_sha256.digest(), length)


def encrypt_bytes(
    plaintext: bytes, media_type: str = "document", media_key: bytes | None = None
) -> tuple[bytes, EncryptedMedia]:
    """In-memory version for small test data: returns (CDN blob, metadata)."""
    with tempfile.TemporaryDirectory() as folder:
        source = Path(folder) / "plain"
        blob_path = Path(folder) / "blob"
        source.write_bytes(plaintext)
        metadata = encrypt_file(source, blob_path, media_type, media_key)
        return blob_path.read_bytes(), metadata
