"""Orchestrator settings, read from the environment (docs/api.md §10).

Every setting is `BVP_*`, except the Tailscale minting variables whose
names are fixed by design §8. A missing required variable stops the
process at startup with a message naming it, instead of failing later on
the first request that needs it.
"""

import ipaddress
from pathlib import Path

from pydantic import AliasChoices, Field, SecretStr, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

from shared.protocol import ENROLL_RATE_LIMIT_REQUESTS, ENROLL_RATE_LIMIT_WINDOW_SECONDS
from shared.whatsapp_media import DEFAULT_MEDIA_BASE_URL

BYTES_PER_GB = 1_000_000_000


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
    dev_mode: bool = False
    port: int = 8000
    public_base_url: str
    tailnet_base_url: str
    trusted_proxy_cidrs: str = "127.0.0.1/32,172.16.0.0/12"
    enroll_rate_limit_requests: int = Field(default=ENROLL_RATE_LIMIT_REQUESTS, ge=1)
    enroll_rate_limit_window_s: int = Field(default=ENROLL_RATE_LIMIT_WINDOW_SECONDS, ge=1)

    # --- VPS disk caps (design §10, §15) ---
    inbox_hold_gb: float = Field(default=22, gt=0)
    inbox_cap_gb: float = Field(default=25, gt=0)
    proxy_evict_min_free_gb: float = Field(default=10, ge=0)

    # --- WhatsApp / Evolution ---
    whatsapp_media_base_url: str = DEFAULT_MEDIA_BASE_URL
    evolution_url: str = "http://evolution:8080"
    # The VPS compose file calls it BVP_EVOLUTION_INSTANCE_TOKEN; accept both.
    evolution_api_key: SecretStr = Field(
        validation_alias=AliasChoices("BVP_EVOLUTION_API_KEY", "BVP_EVOLUTION_INSTANCE_TOKEN")
    )
    evolution_instance: str = "baja-bot"

    # --- Baja app (team names) ---
    baja_app_url: str
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

    if settings.inbox_hold_gb > settings.inbox_cap_gb:
        raise ConfigError("BVP_INBOX_HOLD_GB must not be larger than BVP_INBOX_CAP_GB")
    try:
        settings.trusted_proxy_networks  # noqa: B018 - parse once so typos fail at startup
    except ValueError as error:
        raise ConfigError(f"BVP_TRUSTED_PROXY_CIDRS: {error}") from error
    return settings
