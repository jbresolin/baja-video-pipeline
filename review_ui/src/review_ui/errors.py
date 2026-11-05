"""Turning failures into answers: an error page for browsers, JSON for page scripts.

JSON errors use the API's own shape, {"error": {"code", "message", "details"}}
(docs/api.md §3), so the page scripts read orchestrator errors and the UI's
own errors the same way.

A 401 means "not signed in" (no cookie, or the session ended): a page
request is sent to the login page, which comes back here afterwards; a
page script gets the JSON error and tells the user to sign in again.
"""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse, Response
from markupsafe import Markup

from review_ui.catalog import has_text, text
from review_ui.orchestrator_client import OrchestratorError, OrchestratorUnavailableError
from review_ui.request_context import is_api_request, render_page
from review_ui.session import clear_session_cookie, login_redirect_target

# 409 errors that mean "what you see is out of date": the job page reloads
# and tells the reviewer, instead of letting them retry the same request.
RELOAD_ON_CODES = {"version_conflict", "illegal_transition"}


def page_title(status_code: int) -> str:
    """Friendlier page titles for the statuses a reviewer may run into."""
    key = f"error_page.title.{status_code}"
    return text(key) if has_text(key) else text("error_page.title.other")


def code_text(code: str) -> str:
    """An API error code in words (the code itself is never translated)."""
    key = f"error.{code}"
    return text(key) if has_text(key) else text("error.other")


def error_text(code: str, message: str) -> str:
    """What a person reads about a refusal: the code in words, then the orchestrator's details.

    The orchestrator's message is English and names what was wrong (which
    field, which user...), so it is kept, in parentheses.
    """
    return f"{code_text(code)} ({message})"


def error_markup(code: str, message: str) -> Markup:
    """The same for an HTML page: the orchestrator's sentence is marked as English."""
    return Markup('{} (<span lang="en">{}</span>)').format(code_text(code), message)


class UiRequestError(Exception):
    """A request the UI itself rejects before calling the orchestrator.

    Unlike the orchestrator's, its message is already pt-BR (from the catalog).
    """

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


def error_response(
    request: Request,
    status_code: int,
    code: str,
    message: str,
    details: dict | None = None,
    *,
    people_text: str | None = None,
) -> Response:
    """people_text: the pt-BR sentence to show. None means "the code in words,
    plus the orchestrator's (English) message as details"."""
    if is_api_request(request):
        body = {
            "error": {"code": code, "message": message, "details": details or {}},
            # What the page script shows (pt-BR).
            "text": people_text or error_text(code, message),
            # Tells the page script to reload the job (see RELOAD_ON_CODES).
            "reload": code in RELOAD_ON_CODES,
        }
        return JSONResponse(body, status_code=status_code)
    if status_code == 401:
        # 303: the browser follows with a GET, whatever the method was.
        response = RedirectResponse(login_redirect_target(request), status_code=303)
        clear_session_cookie(response, request.app.state.settings.session_cookie_secure)
        return response
    context = {
        "title": page_title(status_code),
        "status_code": status_code,
        "code": code,
        "explanation": people_text or code_text(code),
        # The orchestrator's own words, shown as details (marked English).
        "detail": None if people_text else message,
    }
    return render_page(request, "error.html", context, status_code=status_code)


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(OrchestratorError)
    def _orchestrator_error(request: Request, error: OrchestratorError) -> Response:
        # Same status and code as the orchestrator gave, so 404 stays 404
        # and 409 stays 409 for the page script.
        return error_response(request, error.status_code, error.code, error.message, error.details)

    @app.exception_handler(OrchestratorUnavailableError)
    def _orchestrator_unavailable(request: Request, error: OrchestratorUnavailableError):
        return error_response(request, 502, "upstream_error", str(error))

    @app.exception_handler(UiRequestError)
    def _ui_request_error(request: Request, error: UiRequestError) -> Response:
        return error_response(
            request, error.status_code, error.code, error.message, people_text=error.message
        )

    @app.exception_handler(RequestValidationError)
    def _validation_error(request: Request, error: RequestValidationError) -> Response:
        # FastAPI's default is a 422 in its own shape; the API uses 400.
        message = "; ".join(str(item.get("msg")) for item in error.errors())
        return error_response(request, 400, "validation_error", message)
