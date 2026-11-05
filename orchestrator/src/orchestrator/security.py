"""Tokens, passwords and the per-IP rate limits.

* Bearer, session and enrollment tokens are random, URL-safe and shown
  once; only their SHA-256 is stored, so a copy of the DB grants nothing.
* Passwords of review UI users are bcrypt hashes in the users table.
  bcrypt is slow on purpose, so it runs once per sign-in; every later
  request carries the session token instead.
* /enroll and /auth/login allow N requests per source IP per window
  (docs/api.md §2.2).
"""

import hashlib
import ipaddress
import secrets
import threading
import uuid
from collections import deque
from datetime import datetime, timedelta

import bcrypt

TOKEN_BYTES = 32


def new_id() -> str:
    """Opaque id for jobs, workers and leases (UUID4 hex, docs/api.md §1)."""
    return uuid.uuid4().hex


def new_secret_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


def token_sha256(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def secrets_match(given: str, expected: str) -> bool:
    """Constant-time comparison, so response timing reveals nothing about the secret."""
    return secrets.compare_digest(given.encode(), expected.encode())


def hash_password(password: str, rounds: int) -> str:
    """A bcrypt hash ($2b$) of the password, for the users table."""
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=rounds)).decode()


def password_matches(password: str, password_hash: str) -> bool:
    """Check a password against a bcrypt hash ($2a$, $2b$ or $2y$).

    Python's bcrypt only knows $2b$; $2a$ and $2y$ hashes (from Caddy's
    `hash-password` or `htpasswd -B`, imported from reviewers.txt) are the
    same algorithm with a different label, so relabel before checking.
    """
    normalized = "$2b$" + password_hash[4:]
    try:
        return bcrypt.checkpw(password.encode(), normalized.encode())
    except ValueError:  # not a bcrypt hash at all
        return False


class SlidingWindowRateLimiter:
    """At most `max_requests` per key in any `window_s` seconds. Every attempt counts."""

    def __init__(self, max_requests: int, window_s: int) -> None:
        self._max_requests = max_requests
        self._window = timedelta(seconds=window_s)
        self._attempts: dict[str, deque[datetime]] = {}
        self._lock = threading.Lock()

    def try_acquire(self, key: str, now: datetime) -> int | None:
        """Count one request. None if allowed, else the seconds to wait (for Retry-After)."""
        with self._lock:
            attempts = self._attempts.setdefault(key, deque())
            while attempts and attempts[0] <= now - self._window:
                attempts.popleft()
            if len(attempts) >= self._max_requests:
                wait = (attempts[0] + self._window - now).total_seconds()
                return max(1, int(wait + 0.999))
            attempts.append(now)
            return None


def client_ip_for_rate_limit(
    peer_address: str | None,
    forwarded_for: str | None,
    trusted_proxies: list[ipaddress.IPv4Network | ipaddress.IPv6Network],
) -> str:
    """The caller's IP: the last X-Forwarded-For hop if the peer is a trusted proxy (Caddy)."""
    peer = peer_address or "unknown"
    try:
        peer_ip = ipaddress.ip_address(peer)
    except ValueError:
        return peer
    if forwarded_for and any(peer_ip in network for network in trusted_proxies):
        last_hop = forwarded_for.split(",")[-1].strip()
        if last_hop:
            return last_hop
    return peer
