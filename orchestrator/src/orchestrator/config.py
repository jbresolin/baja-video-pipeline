"""Orchestrator settings, read from the environment (docs/api.md §10).

Every setting is `BVP_*`, except the Tailscale minting variables whose
names are fixed by design §8. A missing required variable stops the
process at startup with a message naming it, instead of failing later on
the first request that needs it.
"""

import ipaddress
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import AliasChoices, Field, SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from shared.protocol import ENROLL_RATE_LIMIT_REQUESTS, ENROLL_RATE_LIMIT_WINDOW_SECONDS
from shared.whatsapp_media import DEFAULT_MEDIA_BASE_URL

BYTES_PER_GB = 1_000_000_000

# BVP_DEV_MODE=1 is refused unless BVP_PUBLIC_BASE_URL points at one of
# these hosts: dev mode serves raw files without Caddy and enables
# /intake/test-upload, neither of which may ever run on the VPS.
LOCAL_HOSTNAMES = frozenset({"localhost", "127.0.0.1", "::1"})


class ConfigError(Exception):
    """The environment is incomplete or wrong. The message is meant for a human."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="BVP_",
        # Tests build Settings(...) with field names; production uses env names.
        validate_by_name=True,
        validate_by_alias=True,
        extra="ignore",
    )

    # --- Paths ---
    data_dir: Path = Path("/srv/pipeline")
    reviewers_file: Path = Path("/srv/pipeline/reviewers.txt")

    # --- Secrets (required) ---
    admin_token: SecretStr
    webhook_secret: SecretStr

    # --- Server ---
    # Laptop only (see LOCAL_HOSTNAMES): /intake/test-upload and the
    # orchestrator serving /inbox /outbox /proxies /thumbs itself.
    dev_mode: bool = False
    port: int = 8000
    public_base_url: str
    tailnet_base_url: str
    # 10.201.0.0/24 = baja-edge, where the base stack's Caddy reaches us (design §20).
    trusted_proxy_cidrs: str = "127.0.0.1/32,10.201.0.0/24,172.16.0.0/12"
    enroll_rate_limit_requests: int = Field(default=ENROLL_RATE_LIMIT_REQUESTS, ge=1)
    enroll_rate_limit_window_s: int = Field(default=ENROLL_RATE_LIMIT_WINDOW_SECONDS, ge=1)

    # --- VPS disk caps (design §10, §15) ---
    inbox_hold_gb: float = Field(default=22, gt=0)
    inbox_cap_gb: float = Field(default=25, gt=0)
    proxy_evict_min_free_gb: float = Field(default=10, ge=0)

    # --- WhatsApp / Evolution ---
    whatsapp_media_base_url: str = DEFAULT_MEDIA_BASE_URL
    evolution_url: str = "http://evolution:8080"
    # BVP_EVOLUTION_API_KEY is the canonical name (design §17); the older
    # BVP_EVOLUTION_INSTANCE_TOKEN is accepted as an alias.
    evolution_api_key: SecretStr = Field(
        validation_alias=AliasChoices("BVP_EVOLUTION_API_KEY", "BVP_EVOLUTION_INSTANCE_TOKEN")
    )
    evolution_instance: str = "baja-bot"

    # --- Team names (design §17): CSV file first, then the Baja app. Both optional. ---
    teams_file: Path | None = None
    baja_app_url: str = ""
    baja_app_token: SecretStr = SecretStr("")

    # --- Tailscale auth-key minting (infra/tailnet/mint_auth_key.md) ---
    tailscale_oauth_client_id: str = Field(validation_alias="TAILSCALE_OAUTH_CLIENT_ID")
    tailscale_oauth_client_secret: SecretStr = Field(
        validation_alias="TAILSCALE_OAUTH_CLIENT_SECRET"
    )
    tailscale_tailnet: str = Field(default="-", validation_alias="TAILSCALE_TAILNET")
    tailscale_api_base: str = Field(
        default="https://api.tailscale.com", validation_alias="TAILSCALE_API_BASE"
    )

    # --- Derived values ---

    @field_validator("teams_file", mode="before")
    @classmethod
    def _empty_teams_file_means_none(cls, value):
        # Compose passes BVP_TEAMS_FILE= (empty) when it is not configured.
        return None if value == "" else value

    @property
    def inbox_hold_bytes(self) -> int:
        return int(self.inbox_hold_gb * BYTES_PER_GB)

    @property
    def inbox_cap_bytes(self) -> int:
        return int(self.inbox_cap_gb * BYTES_PER_GB)

    @property
    def proxy_evict_min_free_bytes(self) -> int:
        return int(self.proxy_evict_min_free_gb * BYTES_PER_GB)

    @property
    def trusted_proxy_networks(self) -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
        return [
            ipaddress.ip_network(cidr.strip(), strict=False)
            for cidr in self.trusted_proxy_cidrs.split(",")
            if cidr.strip()
        ]


def environment_name_for(field_or_alias: str) -> str:
    """The environment variable a settings field is read from, for error messages."""
    field = Settings.model_fields.get(field_or_alias)
    if field is None:
        # pydantic reports aliased fields under their alias, which is the env name.
        return field_or_alias.upper()
    alias = field.validation_alias
    if isinstance(alias, str):
        return alias
    if isinstance(alias, AliasChoices):
        return " or ".join(str(choice) for choice in alias.choices)
    return f"BVP_{field_or_alias.upper()}"


def is_local_url(url: str) -> bool:
    """True if the URL's host is this machine (localhost, 127.0.0.1 or ::1)."""
    return (urlsplit(url).hostname or "").lower() in LOCAL_HOSTNAMES


def load_settings(**overrides) -> Settings:
    """Read the environment (plus `overrides`, for tests); raise ConfigError if incomplete."""
    try:
        settings = Settings(**overrides)
    except ValidationError as error:
        missing = []
        invalid = []
        for problem in error.errors():
            name = environment_name_for(str(problem["loc"][0]))
            if problem["type"] == "missing":
                missing.append(name)
            else:
                invalid.append(f"{name}: {problem['msg']}")
        lines = []
        if missing:
            lines.append("missing required environment variable(s): " + ", ".join(missing))
        if invalid:
            lines.append("invalid environment variable(s): " + "; ".join(invalid))
        raise ConfigError("\n".join(lines)) from error

    if settings.dev_mode and not is_local_url(settings.public_base_url):
        raise ConfigError(
            "BVP_DEV_MODE=1 is only allowed on a developer machine: BVP_PUBLIC_BASE_URL must be "
            f"http://localhost or http://127.0.0.1, not {settings.public_base_url!r}. "
            "Remove BVP_DEV_MODE from the VPS environment (orchestrator.env)."
        )
    if settings.inbox_hold_gb > settings.inbox_cap_gb:
        raise ConfigError("BVP_INBOX_HOLD_GB must not be larger than BVP_INBOX_CAP_GB")
    try:
        settings.trusted_proxy_networks  # noqa: B018 - parse once so typos fail at startup
    except ValueError as error:
        raise ConfigError(f"BVP_TRUSTED_PROXY_CIDRS: {error}") from error
    return settings
