"""Tokens, reviewer passwords and the /enroll rate limit.

* Bearer and enrollment tokens are random, URL-safe and shown once; only
  their SHA-256 is stored, so a copy of the DB grants nothing.
* Reviewer passwords are bcrypt hashes in BVP_REVIEWERS_FILE. bcrypt is
  slow on purpose (cost 14 ≈ 1 s), so a successful check is remembered in
  memory: the review UI sends Basic auth on every request. The file is
  re-read when it changes, so reviewers are added without a restart.
* /enroll allows N requests per source IP per window (docs/api.md §2.2).
"""

import hashlib
import ipaddress
import logging
import secrets
import threading
import uuid
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path

import bcrypt

from shared.reviewers_file import ReviewersFileError, load_reviewers_file

logger = logging.getLogger(__name__)

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


class ReviewerDirectory:
    """Checks reviewer usernames and passwords against the reviewers file.

    Built with from_file(), it re-reads the file whenever its modification
    time or size changes, checked on every login, like clients/teams.py does
    for BVP_TEAMS_FILE: adding or removing a reviewer needs no restart.
    A missing file means "no reviewers". A file that no longer parses (say,
    half-edited) is logged and the previous version stays in force, so a
    typo cannot lock every reviewer out.
    """

    def __init__(self, password_hashes: dict[str, str], path: Path | None = None) -> None:
        self._password_hashes = password_hashes
        # (username, SHA-256 of a password) pairs that already passed bcrypt.
        # Cleared on every reload, so a removed or re-hashed reviewer is
        # checked against the new file, never against this memory.
        self._verified: set[tuple[str, str]] = set()
        self._lock = threading.Lock()
        self._path = path
        # (modification time in ns, size) of the version in _password_hashes.
        self._loaded_signature = _file_signature(path) if path else None

    @classmethod
    def from_file(cls, path: Path) -> "ReviewerDirectory":
        # At startup a bad file is an error (the orchestrator refuses to start).
        return cls(load_reviewers_file(path), path)

    @property
    def usernames(self) -> list[str]:
        with self._lock:
            self._reload_if_changed()
            return sorted(self._password_hashes)

    def check(self, username: str, password: str) -> bool:
        remembered = (username, token_sha256(password))
        with self._lock:
            self._reload_if_changed()
            password_hash = self._password_hashes.get(username)
            if password_hash is None:
                return False
            if remembered in self._verified:
                return True
        # Python's bcrypt only knows $2b$; $2a$ and $2y$ hashes are the same
        # algorithm with a different label, so relabel before checking.
        normalized = "$2b$" + password_hash[4:]
        if not bcrypt.checkpw(password.encode(), normalized.encode()):
            return False
        with self._lock:
            # Only remember it if the file did not change during bcrypt.
            if self._password_hashes.get(username) == password_hash:
                self._verified.add(remembered)
        return True

    def _reload_if_changed(self) -> None:
        """Re-read the file if it changed on disk. Call with self._lock held."""
        if self._path is None:
            return
        signature = _file_signature(self._path)
        if signature == self._loaded_signature:
            return
        if signature is None:
            logger.warning("reviewers file %s is gone: no reviewer can log in", self._path)
            password_hashes = {}
        else:
            try:
                password_hashes = load_reviewers_file(self._path)
            except (OSError, ReviewersFileError) as error:
                logger.warning("reviewers file not reloaded, keeping the previous one: %s", error)
                return
        self._password_hashes = password_hashes
        self._verified.clear()
        self._loaded_signature = signature
        logger.info("loaded %d reviewer(s) from %s", len(password_hashes), self._path)


def _file_signature(path: Path) -> tuple[int, int] | None:
    """(modification time in ns, size), or None if the file does not exist."""
    try:
        status = path.stat()
    except FileNotFoundError:
        return None
    return (status.st_mtime_ns, status.st_size)


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
