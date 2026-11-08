"""Build the orchestrator: context (DB, clients, clock) + FastAPI app.

    settings = load_settings()
    app = create_app(build_context(settings))

Tests call build_context() with fakes for the clock and the four outside
services (WhatsApp CDN, Evolution, Tailscale, the Baja results API) and pass
run_background_tasks=False, then drive the sweepers by hand.
"""

import contextlib
import logging

import httpx
from fastapi import FastAPI

from orchestrator.api import (
    routes_admin_catalog,
    routes_admin_operations,
    routes_admin_people,
    routes_auth,
    routes_dev_files,
    routes_intake_enrollment,
    routes_metrics,
    routes_requested_clips,
    routes_review_delivery,
    routes_worker_loop,
)
from orchestrator.api.error_handlers import install_error_handlers
from orchestrator.background.runner import start_background_tasks
from orchestrator.clients.evolution import EvolutionClient
from orchestrator.clients.results_api import ResultsApiClient
from orchestrator.clients.tailscale import TailscaleClient
from orchestrator.clock import Clock
from orchestrator.config import Settings
from orchestrator.context import OrchestratorContext
from orchestrator.db.database import open_database
from orchestrator.domain.app_settings import AdminSettingsStore
from orchestrator.domain.first_start import run_first_start_imports
from orchestrator.security import SlidingWindowRateLimiter
from orchestrator.storage import VpsLayout

logger = logging.getLogger(__name__)

# Generous for 2 GB media downloads: only the gap between bytes is timed.
MEDIA_TIMEOUT = httpx.Timeout(connect=10, read=60, write=60, pool=10)
API_TIMEOUT = httpx.Timeout(10)


def build_context(
    settings: Settings,
    *,
    clock: Clock | None = None,
    media_http_client: httpx.Client | None = None,
    evolution_http_client: httpx.Client | None = None,
    tailscale_http_client: httpx.Client | None = None,
    results_api_http_client: httpx.Client | None = None,
    layout: VpsLayout | None = None,
) -> OrchestratorContext:
    """Create the folders and DB under BVP_DATA_DIR and wire every dependency.

    On a new database this also imports reviewers.txt, the teams file and
    the provas used by existing jobs (domain/first_start.py).
    """
    clock = clock or Clock()
    layout = layout or VpsLayout(
        settings.data_dir, settings.public_base_url, settings.tailnet_base_url
    )
    layout.create_folders()
    database = open_database(settings.data_dir)
    run_first_start_imports(database, settings, clock.now())
    return OrchestratorContext(
        settings=settings,
        database=database,
        clock=clock,
        layout=layout,
        admin_settings=AdminSettingsStore(database, settings, clock),
        media_http_client=media_http_client or httpx.Client(timeout=MEDIA_TIMEOUT),
        evolution=EvolutionClient(
            evolution_http_client or httpx.Client(timeout=API_TIMEOUT),
            settings.evolution_url,
            settings.evolution_api_key.get_secret_value(),
            settings.evolution_instance,
        ),
        tailscale=TailscaleClient(
            tailscale_http_client or httpx.Client(timeout=API_TIMEOUT),
            settings.tailscale_api_base,
            settings.tailscale_oauth_client_id,
            settings.tailscale_oauth_client_secret.get_secret_value(),
            settings.tailscale_tailnet,
            clock,
        ),
        results_api=ResultsApiClient(results_api_http_client or httpx.Client(timeout=API_TIMEOUT)),
        enroll_rate_limiter=SlidingWindowRateLimiter(
            settings.enroll_rate_limit_requests, settings.enroll_rate_limit_window_s
        ),
        login_rate_limiter=SlidingWindowRateLimiter(
            settings.login_rate_limit_requests, settings.login_rate_limit_window_s
        ),
    )


def create_app(context: OrchestratorContext, *, run_background_tasks: bool = True) -> FastAPI:
    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        tasks = start_background_tasks(context) if run_background_tasks else []
        logger.info("orchestrator started; data dir %s", context.settings.data_dir)
        yield
        for task in tasks:
            task.cancel()
        context.database.close()

    app = FastAPI(
        title="Baja video pipeline — orchestrator",
        description="Implements orchestrator/openapi.yaml (docs/api.md).",
        lifespan=lifespan,
    )
    app.state.context = context
    install_error_handlers(app)
    app.include_router(routes_intake_enrollment.router)
    app.include_router(routes_worker_loop.router)
    app.include_router(routes_review_delivery.router)
    app.include_router(routes_metrics.router)
    app.include_router(routes_auth.router)
    app.include_router(routes_requested_clips.router)
    app.include_router(routes_admin_people.router)
    app.include_router(routes_admin_catalog.router)
    app.include_router(routes_admin_operations.router)
    if context.settings.dev_mode:
        # Caddy's static file routes, for a laptop with no Caddy (design §18).
        # load_settings() refuses dev mode unless BVP_PUBLIC_BASE_URL is localhost.
        app.include_router(routes_dev_files.router)
    return app
