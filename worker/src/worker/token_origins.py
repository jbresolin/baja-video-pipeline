"""Which URLs may see our bearer token (design §17, docs/api.md §5).

The token is the worker's identity, so it goes to exactly two origins:

  * `orchestrator_url` from worker.json (the API), and
  * `tailnet_base_url` from the /enroll answer (Caddy's tailnet listener,
    which serves /inbox/* and /outbox/*).

An "origin" is (scheme, host, port), as in browsers: "http://a:8080/x" and
"http://a:8080/y" share one; "http://a:8710/raw/1" (a peer's file server
on the same machine) does not. Peers (:8710), the studio (:8720) and any
other URL an offer might carry never get the token.
"""

from collections.abc import Iterable
from urllib.parse import urlsplit

# What a URL without an explicit port means.
DEFAULT_PORT_BY_SCHEME = {"http": 80, "https": 443}

Origin = tuple[str, str, int]


def url_origin(url: str) -> Origin | None:
    """(scheme, host, port) of an absolute URL, lowercased; None if it has none.

    "http://Caddy.test/inbox/a.mp4" → ("http", "caddy.test", 80).
    """
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()  # .hostname is already lowercase; be explicit
    if not scheme or not host:
        return None
    try:
        port = parts.port
    except ValueError:  # e.g. "http://host:notaport/"
        return None
    if port is None:
        port = DEFAULT_PORT_BY_SCHEME.get(scheme)
    if port is None:
        return None
    return (scheme, host, port)


def may_send_bearer_token(url: str, trusted_base_urls: Iterable[str | None]) -> bool:
    """True only when `url` has the same origin as one of `trusted_base_urls`.

    `None` entries are skipped, so an older worker.json without
    `tailnet_base_url` simply trusts `orchestrator_url` alone.
    """
    target = url_origin(url)
    if target is None:
        return False
    return any(base and url_origin(base) == target for base in trusted_base_urls)
