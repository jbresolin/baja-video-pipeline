"""Mint single-use Tailscale auth keys for enrollment (infra/tailnet/mint_auth_key.md).

Two calls: trade the OAuth client credentials for an access token (cached
in memory until a minute before it expires), then create one auth key that
is single-use, pre-authorized, non-ephemeral and tagged. The base URL comes
from TAILSCALE_API_BASE so tests can point it at a fake.
"""

import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

import httpx

from orchestrator.clock import Clock
from shared.models import WorkerRole

logger = logging.getLogger(__name__)

UNUSED_KEY_EXPIRY_SECONDS = 3600
# The spec's fallback if Tailscale rejects a one-hour expiry.
FALLBACK_KEY_EXPIRY_SECONDS = 86400
TOKEN_REFRESH_MARGIN_SECONDS = 60
# Tailscale only accepts letters, digits, hyphens and spaces, at most 50 characters.
KEY_DESCRIPTION_PATTERN = re.compile(r"^[A-Za-z0-9 -]{1,50}$")
TAG_BY_ROLE = {
    WorkerRole.MASTER: "tag:worker",
    WorkerRole.WORKER: "tag:worker",
    WorkerRole.STUDIO: "tag:studio",
}
# 429 and 5xx: retry with backoff.
RETRY_DELAYS_SECONDS = (1.0, 3.0)


class TailscaleError(Exception):
    """Minting failed. The enrollment token must stay unspent."""


@dataclass(frozen=True)
class MintedAuthKey:
    key: str
    key_id: str
    tags: list[str]


class TailscaleClient:
    def __init__(
        self,
        http_client: httpx.Client,
        api_base: str,
        client_id: str,
        client_secret: str,
        tailnet: str,
        clock: Clock,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._http = http_client
        self._api_base = api_base.rstrip("/")
        self._client_id = client_id
        self._client_secret = client_secret
        self._tailnet = tailnet
        self._clock = clock
        self._sleep = sleep
        self._access_token: str | None = None
        self._access_token_valid_until: datetime | None = None

    def mint_auth_key(self, role: WorkerRole, description: str) -> MintedAuthKey:
        if not KEY_DESCRIPTION_PATTERN.match(description):
            raise ValueError(f"invalid Tailscale key description {description!r}")
        tags = [TAG_BY_ROLE[role]]
        expiry = UNUSED_KEY_EXPIRY_SECONDS
        refreshed_token = False
        attempt = 0
        while True:
            response = self._post_create_key(tags, description, expiry)
            if response.status_code == 200:
                body = response.json()
                return MintedAuthKey(key=body["key"], key_id=body.get("id", ""), tags=tags)
            if response.status_code == 401 and not refreshed_token:
                # Token expired or was revoked: get a new one and retry once.
                self._access_token = None
                refreshed_token = True
                continue
            if response.status_code == 400 and expiry != FALLBACK_KEY_EXPIRY_SECONDS:
                logger.warning("Tailscale rejected the key request: %s", response.text)
                expiry = FALLBACK_KEY_EXPIRY_SECONDS
                continue
            if _is_retryable(response.status_code) and attempt < len(RETRY_DELAYS_SECONDS):
                self._sleep(RETRY_DELAYS_SECONDS[attempt])
                attempt += 1
                continue
            raise TailscaleError(
                f"creating the auth key failed with HTTP {response.status_code}: {response.text}"
            )

    def _post_create_key(self, tags: list[str], description: str, expiry: int) -> httpx.Response:
        body = {
            "keyType": "auth",
            "description": description,
            "capabilities": {
                "devices": {
                    "create": {
                        "reusable": False,
                        "ephemeral": False,
                        "preauthorized": True,
                        "tags": tags,
                    }
                }
            },
            "expirySeconds": expiry,
        }
        try:
            return self._http.post(
                f"{self._api_base}/api/v2/tailnet/{self._tailnet}/keys",
                json=body,
                headers={"Authorization": f"Bearer {self._get_access_token()}"},
            )
        except httpx.HTTPError as error:
            raise TailscaleError(f"cannot reach the Tailscale API: {error}") from error

    def _get_access_token(self) -> str:
        now = self._clock.now()
        if (
            self._access_token is not None
            and self._access_token_valid_until is not None
            and now < self._access_token_valid_until
        ):
            return self._access_token
        try:
            response = self._http.post(
                f"{self._api_base}/api/v2/oauth/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                },
            )
        except httpx.HTTPError as error:
            raise TailscaleError(f"cannot reach the Tailscale API: {error}") from error
        if response.status_code == 401:
            raise TailscaleError(
                "Tailscale refused the OAuth client (check TAILSCALE_OAUTH_CLIENT_ID/SECRET)"
            )
        if response.status_code != 200:
            raise TailscaleError(f"OAuth token request failed with HTTP {response.status_code}")
        body = response.json()
        self._access_token = body["access_token"]
        lifetime = int(body.get("expires_in", 3600)) - TOKEN_REFRESH_MARGIN_SECONDS
        self._access_token_valid_until = now + timedelta(seconds=max(lifetime, 0))
        return self._access_token


def _is_retryable(status_code: int) -> bool:
    return status_code == 429 or status_code >= 500
