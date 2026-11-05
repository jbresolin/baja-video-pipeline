"""GET/PATCH /admin/settings: the editable settings plus a read-only status block.

Secrets stay in .env and are never sent: the status block only says,
per secret, whether it is configured or missing.
"""

from orchestrator.context import OrchestratorContext
from orchestrator.domain.audit import AdminActor
from orchestrator.domain.fleet import server_disk
from shared.models import SecretState, SettingsResponse, SettingsStatus, UpdateSettingsRequest


def secret_states(context: OrchestratorContext) -> dict[str, SecretState]:
    """Configured or missing, per secret (by its .env name). Values are never read out."""
    settings = context.settings
    values = {
        "BVP_ADMIN_TOKEN": settings.admin_token,
        "BVP_WEBHOOK_SECRET": settings.webhook_secret,
        "BVP_EVOLUTION_API_KEY": settings.evolution_api_key,
        "TAILSCALE_OAUTH_CLIENT_SECRET": settings.tailscale_oauth_client_secret,
    }
    return {
        name: SecretState.CONFIGURED if value.get_secret_value().strip() else SecretState.MISSING
        for name, value in values.items()
    }


def settings_page(context: OrchestratorContext) -> SettingsResponse:
    # Asks Evolution live (two short GETs), so the page shows the real state.
    status = SettingsStatus(
        evolution=context.evolution.instance_status(),
        secrets=secret_states(context),
        server_disk=server_disk(context),
        inbox_cap_gb=context.settings.inbox_cap_gb,
    )
    return SettingsResponse(settings=context.admin_settings.current, status=status)


def update_settings(
    context: OrchestratorContext, actor: AdminActor, request: UpdateSettingsRequest
) -> SettingsResponse:
    context.admin_settings.update(actor, request, context.clock.now())
    return settings_page(context)
