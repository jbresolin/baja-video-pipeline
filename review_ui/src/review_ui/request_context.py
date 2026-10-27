"""Per-request helpers: the reviewer's orchestrator client, their name, page rendering."""

import base64
import binascii

from fastapi import Request
from fastapi.responses import HTMLResponse

from review_ui.orchestrator_client import OrchestratorClient
from shared.models import utc_now


def orchestrator_for(request: Request) -> OrchestratorClient:
    """An orchestrator client that acts as the reviewer who sent this request.

    Caddy already checked the browser's `Authorization: Basic …` header; we
    pass it on untouched, and the orchestrator checks it again (docs/api.md §2).
    """
    return OrchestratorClient(
        request.app.state.orchestrator_http_client,
        request.headers.get("authorization"),
    )


def reviewer_name(request: Request) -> str | None:
    """Who is signed in, for the page header only (never used for access decisions).

    Caddy adds X-Reviewer with the user name it authenticated. Without Caddy
    (local development) we read the user name out of the Basic header.
    """
    from_caddy = request.headers.get("x-reviewer")
    if from_caddy:
        return from_caddy
    scheme, _, encoded = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "basic":
        return None
    try:
        decoded = base64.b64decode(encoded, validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        return None
    username, _, _ = decoded.partition(":")
    return username or None


def render_page(
    request: Request, template_name: str, context: dict, status_code: int = 200
) -> HTMLResponse:
    """Render a template with the values every page needs (reviewer, settings, time)."""
    templates = request.app.state.templates
    full_context = {
        "reviewer": reviewer_name(request),
        "settings": request.app.state.settings,
        "now": utc_now(),
        **context,
    }
    return templates.TemplateResponse(request, template_name, full_context, status_code=status_code)


def is_api_request(request: Request) -> bool:
    """True for the JSON endpoints the page scripts call (they want JSON errors)."""
    return request.url.path.startswith("/review/api/")
