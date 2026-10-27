"""Tests for shared.whatsapp_media's URL choice and key checks.

Which URL do we fetch WhatsApp media from? The webhook gives a full `url`
and a `directPath`. We only trust URLs on WhatsApp's own media host;
anything else is rebuilt from directPath on that host. That way a forged
webhook cannot make the orchestrator download from any server it likes.
"""

import pytest

from shared.models import ReceivedAs
from shared.whatsapp_media import MediaDownloadError, derive_media_keys, media_download_url


def test_trusted_webhook_url_is_used_as_is():
    url = "https://mmg.whatsapp.net/v/t62/abc.enc?oh=1"
    assert media_download_url(url, "/v/t62/abc.enc") == url


def test_untrusted_url_is_replaced_by_the_direct_path():
    # A forged webhook could point us at any server (for example an
    # internal one). We only trust WhatsApp's host and otherwise rebuild
    # the URL from directPath on that host.
    chosen = media_download_url("http://169.254.169.254/latest", "/v/t62/abc.enc")
    assert chosen == "https://mmg.whatsapp.net/v/t62/abc.enc"


def test_dev_base_url_lets_the_fake_serve_media_locally():
    assert media_download_url(None, "/blob.enc", "http://127.0.0.1:8790") == (
        "http://127.0.0.1:8790/blob.enc"
    )


def test_no_usable_url_is_an_error():
    with pytest.raises(MediaDownloadError):
        media_download_url("http://evil.example/x", None)


def test_media_key_must_be_32_bytes():
    with pytest.raises(ValueError):
        derive_media_keys(b"short", ReceivedAs.DOCUMENT)
