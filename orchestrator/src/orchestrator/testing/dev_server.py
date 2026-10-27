"""Run the orchestrator locally with fakes for Evolution, Tailscale and the Baja app.

    uv run python -m orchestrator.testing.dev_server

Real HTTP on http://127.0.0.1:8000 and a real clock, so you can drive it
with curl and with orchestrator/fakes/fake_evolution_webhook.py (whose fake
WhatsApp CDN on :8790 is the media base URL used here). Bot replies are
printed instead of sent; /enroll mints fake Tailscale keys; car numbers
1-99 resolve to "Equipe <n>".

Defaults (override any with the usual BVP_* variables):
    data dir  .scratch/bvp-dev        admin token  dev-admin
    webhook   dev-secret              reviewer     ana / ana
    BVP_DEV_MODE=1 (POST /intake/test-upload enabled)
"""

import logging
import os
from pathlib import Path

import httpx
import uvicorn

from orchestrator.app import build_context, create_app
from orchestrator.config import load_settings
from orchestrator.testing.fakes import (
    FAKE_BAJA_APP_URL,
    FAKE_EVOLUTION_URL,
    FAKE_TAILSCALE_API_BASE,
    FAKE_TAILSCALE_CLIENT_ID,
    FAKE_TAILSCALE_CLIENT_SECRET,
    FakeBajaApp,
    FakeEvolutionApi,
    FakeTailscaleApi,
)
from orchestrator.testing.harness import write_reviewers_file

logger = logging.getLogger("dev_server")

DEV_DEFAULTS = {
    "BVP_DATA_DIR": ".scratch/bvp-dev",
    "BVP_ADMIN_TOKEN": "dev-admin",
    "BVP_WEBHOOK_SECRET": "dev-secret",
    "BVP_DEV_MODE": "1",
    "BVP_PUBLIC_BASE_URL": "http://127.0.0.1:8000",
    "BVP_TAILNET_BASE_URL": "http://127.0.0.1:8000",
    "BVP_WHATSAPP_MEDIA_BASE_URL": "http://127.0.0.1:8790",
    "BVP_EVOLUTION_URL": FAKE_EVOLUTION_URL,
    "BVP_EVOLUTION_API_KEY": "dev-evolution-key",
    "BVP_BAJA_APP_URL": FAKE_BAJA_APP_URL,
    "TAILSCALE_OAUTH_CLIENT_ID": FAKE_TAILSCALE_CLIENT_ID,
    "TAILSCALE_OAUTH_CLIENT_SECRET": FAKE_TAILSCALE_CLIENT_SECRET,
    "TAILSCALE_API_BASE": FAKE_TAILSCALE_API_BASE,
}


class PrintingEvolutionApi(FakeEvolutionApi):
    """Records like the test fake, and also logs each bot reply."""

    def handle(self, request: httpx.Request) -> httpx.Response:
        response = super().handle(request)
        if self.sent:
            logger.info("bot → %s: %s", self.sent[-1].number, self.sent[-1].text)
        return response


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s")
    for name, value in DEV_DEFAULTS.items():
        os.environ.setdefault(name, value)
    data_dir = Path(os.environ["BVP_DATA_DIR"]).resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    reviewers_file = data_dir / "reviewers.txt"
    if not reviewers_file.exists():
        write_reviewers_file(reviewers_file, {"ana": "ana"})
    os.environ.setdefault("BVP_REVIEWERS_FILE", str(reviewers_file))

    settings = load_settings()
    baja_app = FakeBajaApp(teams={number: f"Equipe {number}" for number in range(1, 100)})
    context = build_context(
        settings,
        evolution_http_client=httpx.Client(transport=PrintingEvolutionApi().transport()),
        tailscale_http_client=httpx.Client(transport=FakeTailscaleApi().transport()),
        baja_app_http_client=httpx.Client(transport=baja_app.transport()),
    )
    logger.info("data dir %s; admin token %s", data_dir, settings.admin_token.get_secret_value())
    uvicorn.run(create_app(context), host="127.0.0.1", port=settings.port)


if __name__ == "__main__":
    main()
