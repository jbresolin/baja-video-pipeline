"""Per-request helpers: the user's orchestrator client, who is signed in, page rendering."""

from fastapi import Request
from fastapi.responses import HTMLResponse

from review_ui.admin_client import AdminClient
from review_ui.orchestrator_client import (
    OrchestratorClient,
    OrchestratorError,
    OrchestratorUnavailableError,
)
from review_ui.session import session_token
from shared.models import CurrentUser, UserRole, utc_now


def _authorization(request: Request) -> str | None:
    token = session_token(request)
    return f"Bearer {token}" if token else None


def orchestrator_for(request: Request) -> OrchestratorClient:
    """An orchestrator client that acts as the user who sent this request.

    The browser's bvp_session cookie becomes `Authorization: Bearer …`; the
    orchestrator checks it (docs/api.md §2). Without a cookie the calls go
    out without credentials and the orchestrator answers 401, which sends
    the browser to the login page (errors.py).
    """
    return OrchestratorClient(
        request.app.state.orchestrator_http_client,
        _authorization(request),
        request.headers.get("x-forwarded-for"),
    )


def admin_client_for(request: Request) -> AdminClient:
    """The same, with the admin pages' calls (the orchestrator checks the role)."""
    return AdminClient(
        request.app.state.orchestrator_http_client,
        _authorization(request),
        request.headers.get("x-forwarded-for"),
    )


def current_user(request: Request) -> CurrentUser | None:
    """Who is signed in (asked once per request), or None.

    For the page header and the Admin link only. Access decisions are made
    by the orchestrator on every call, never from this.
    """
    if not hasattr(request.state, "current_user"):
        user = None
        if session_token(request):
            try:
                user = orchestrator_for(request).me()
            except (OrchestratorError, OrchestratorUnavailableError):
                user = None
        request.state.current_user = user
    return request.state.current_user


def render_page(
    request: Request, template_name: str, context: dict, status_code: int = 200
) -> HTMLResponse:
    """Render a template with the values every page needs (user, settings, time).

    Partial templates (names starting with "_", polled by the page scripts)
    do not show the header, so they skip asking who is signed in.
    """
    templates = request.app.state.templates
    user = None if template_name.startswith("_") else current_user(request)
    full_context = {
        "user": user,
        "is_admin": user is not None and user.role == UserRole.ADMIN,
        "settings": request.app.state.settings,
        "now": utc_now(),
        **context,
    }
    return templates.TemplateResponse(request, template_name, full_context, status_code=status_code)


def render_fragment(request: Request, template_name: str, context: dict) -> str:
    """A piece of a page as HTML text, for a page script to put in place.

    The inline admin actions (routes_admin_inline.py) answer with the rows
    they changed, rendered by the same template the full page includes, so
    the swapped-in rows are exactly what a reload would show.
    """
    templates = request.app.state.templates
    full_context = {"settings": request.app.state.settings, "now": utc_now(), **context}
    return templates.env.get_template(template_name).render(full_context)


def is_api_request(request: Request) -> bool:
    """True for the JSON endpoints the page scripts call (they want JSON errors)."""
    return request.url.path.startswith("/review/api/")
