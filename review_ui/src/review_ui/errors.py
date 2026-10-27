"""Turning failures into answers: an error page for browsers, JSON for page scripts.

JSON errors use the API's own shape, {"error": {"code", "message", "details"}}
(docs/api.md §3), so the page scripts read orchestrator errors and the UI's
own errors the same way.
"""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response

from review_ui.orchestrator_client import OrchestratorError, OrchestratorUnavailableError
from review_ui.request_context import is_api_request, render_page

# Sent with every 401 so a browser that reached the UI without Caddy (local
# development) shows its sign-in dialog. Behind Caddy, Caddy asks first.
BASIC_AUTH_CHALLENGE = {"WWW-Authenticate": 'Basic realm="Baja review", charset="UTF-8"'}

# 409 errors that mean "what you see is out of date": the job page reloads
# and tells the reviewer, instead of letting them retry the same request.
RELOAD_ON_CODES = {"version_conflict", "illegal_transition"}

# Friendlier page titles for the statuses a reviewer may run into.
PAGE_TITLES = {
    401: "Sign-in needed",
    403: "Not allowed",
    404: "Not found",
    409: "Changed by someone else",
    502: "Orchestrator unreachable",
}


class UiRequestError(Exception):
    """A request the UI itself rejects before calling the orchestrator."""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


def error_response(
    request: Request, status_code: int, code: str, message: str, details: dict | None = None
) -> Response:
    headers = BASIC_AUTH_CHALLENGE if status_code == 401 else None
    if is_api_request(request):
        body = {
            "error": {"code": code, "message": message, "details": details or {}},
            # Tells the page script to reload the job (see RELOAD_ON_CODES).
            "reload": code in RELOAD_ON_CODES,
        }
        return JSONResponse(body, status_code=status_code, headers=headers)
    context = {
        "title": PAGE_TITLES.get(status_code, "Something went wrong"),
        "status_code": status_code,
        "code": code,
        "message": message,
    }
    response = render_page(request, "error.html", context, status_code=status_code)
    if headers:
        response.headers.update(headers)
    return response


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
        return error_response(request, error.status_code, error.code, error.message)

    @app.exception_handler(RequestValidationError)
    def _validation_error(request: Request, error: RequestValidationError) -> Response:
        # FastAPI's default is a 422 in its own shape; the API uses 400.
        message = "; ".join(str(item.get("msg")) for item in error.errors())
        return error_response(request, 400, "validation_error", message)
