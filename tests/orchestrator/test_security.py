"""Tests for orchestrator.security: rate limit, source IP, reviewer passwords, tokens."""

import ipaddress
from datetime import UTC, datetime, timedelta

import bcrypt

from orchestrator.security import (
    ReviewerDirectory,
    SlidingWindowRateLimiter,
    client_ip_for_rate_limit,
    new_id,
    token_sha256,
)

NOW = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)
CADDY_NETWORKS = [ipaddress.ip_network("172.16.0.0/12")]


def test_rate_limiter_allows_n_requests_per_window_then_says_how_long_to_wait():
    limiter = SlidingWindowRateLimiter(max_requests=3, window_s=600)
    # Three requests from the same IP are fine (None = allowed).
    for second in range(3):
        assert limiter.try_acquire("1.2.3.4", NOW + timedelta(seconds=second)) is None
    # The fourth, 10 s after the first, must wait until the first one is
    # 600 s old: 590 s from now. That number becomes the Retry-After header.
    assert limiter.try_acquire("1.2.3.4", NOW + timedelta(seconds=10)) == 590
    # Another IP is not affected.
    assert limiter.try_acquire("5.6.7.8", NOW) is None
    # Once the window has passed, requests are allowed again.
    assert limiter.try_acquire("1.2.3.4", NOW + timedelta(seconds=601)) is None


def test_source_ip_is_the_last_forwarded_hop_only_behind_a_trusted_proxy():
    # Behind Caddy (a trusted address), the client is the last X-Forwarded-For entry.
    assert client_ip_for_rate_limit("172.18.0.5", "9.9.9.9, 8.8.8.8", CADDY_NETWORKS) == "8.8.8.8"
    # From anywhere else the header is ignored, or anyone could pick their own IP.
    assert client_ip_for_rate_limit("203.0.113.7", "8.8.8.8", CADDY_NETWORKS) == "203.0.113.7"
    # No header: the TCP peer.
    assert client_ip_for_rate_limit("172.18.0.5", None, CADDY_NETWORKS) == "172.18.0.5"


def test_reviewer_passwords_are_checked_against_bcrypt_hashes():
    # The reviewers file may hold $2y$ hashes (from htpasswd); Python's
    # bcrypt only writes $2b$. They are the same algorithm, so both work.
    hash_2b = bcrypt.hashpw(b"secret", bcrypt.gensalt(rounds=4)).decode()
    hash_2y = "$2y$" + hash_2b[4:]
    directory = ReviewerDirectory({"ana": hash_2b, "bruno": hash_2y})
    assert directory.check("ana", "secret")
    assert directory.check("bruno", "secret")
    assert not directory.check("ana", "wrong")
    assert not directory.check("nobody", "secret")
    # Checking again is answered from memory (bcrypt is slow on purpose).
    assert directory.check("ana", "secret")


def test_ids_and_token_hashes_have_the_documented_shape():
    # docs/api.md §1: ids are opaque UUID4 hex strings.
    assert len(new_id()) == 32
    assert new_id() != new_id()
    # Tokens are stored only as SHA-256 hex.
    assert len(token_sha256("anything")) == 64
