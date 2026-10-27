"""Builds the review UI web app.

    uvicorn --factory review_ui.app:create_app --port 8001

Tests call create_app(settings, orchestrator_http_client=...) with a client
wired to a fake orchestrator, so no network is involved.
"""

from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from review_ui import __version__, routes_api, routes_clips_fleet, routes_job, routes_queue
from review_ui.errors import register_error_handlers
from review_ui.settings import ReviewUiSettings
from review_ui.templating import STATIC_DIR, STATIC_URL_PREFIX, build_templates


def create_app(
    settings: ReviewUiSettings | None = None,
    orchestrator_http_client: httpx.Client | None = None,
) -> FastAPI:
    settings = settings or ReviewUiSettings.from_environment()
    owns_http_client = orchestrator_http_client is None
    http_client = orchestrator_http_client or httpx.Client(
        base_url=settings.orchestrator_url, timeout=settings.orchestrator_timeout_s
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        # Close the connection pool on shutdown, but only if we opened it.
        if owns_http_client:
            http_client.close()

    # No OpenAPI docs: this app serves pages, its JSON routes are internal.
    app = FastAPI(
        title="Baja review UI",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.orchestrator_http_client = http_client
    app.state.templates = build_templates(settings)

    app.mount(STATIC_URL_PREFIX, StaticFiles(directory=STATIC_DIR), name="static")
    app.include_router(routes_queue.router)
    app.include_router(routes_job.router)
    app.include_router(routes_clips_fleet.router)
    app.include_router(routes_api.router)
    register_error_handlers(app)

    @app.get("/healthz", include_in_schema=False)
    def health() -> dict[str, str]:
        # For the container health check. Not under /review, so Caddy never
        # routes it to the public.
        return {"status": "ok"}

    return app
