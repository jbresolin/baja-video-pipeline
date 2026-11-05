"""Sign-in and sign-out pages (/review/login, /review/logout).

The password goes to the orchestrator (POST /auth/login), which answers
with a session token; this page puts the token in the bvp_session cookie
and sends the browser on to the page it first asked for.
"""

import contextlib

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from pydantic import ValidationError

from review_ui.catalog import text
from review_ui.errors import error_markup
from review_ui.forms import read_form
from review_ui.orchestrator_client import OrchestratorError, OrchestratorUnavailableError
from review_ui.request_context import orchestrator_for, render_page
from review_ui.session import (
    LOGIN_PATH,
    clear_session_cookie,
    safe_next_path,
    set_session_cookie,
)

router = APIRouter()

# What the login page says when signing in did not work.
LOGIN_ERRORS = {
    401: text("login.wrong_password"),
    429: text("login.too_many_attempts"),
}
UNAVAILABLE = text("login.unavailable")


def _login_page(request: Request, next_path: str, error: str | None, status_code: int = 200):
    context = {"next_path": next_path, "error": error, "active_nav": None}
    return render_page(request, "login.html", context, status_code=status_code)


@router.get(LOGIN_PATH, response_class=HTMLResponse)
def login_page(request: Request, next: str | None = None) -> HTMLResponse:  # noqa: A002
    return _login_page(request, safe_next_path(next), None)


@router.post(LOGIN_PATH)
def login(request: Request, form: dict = Depends(read_form)) -> Response:
    next_path = safe_next_path(form.get("next"))
    username, password = form.get("username", "").strip(), form.get("password", "")
    try:
        session = orchestrator_for(request).login(username, password)
    except ValidationError:  # empty username or password
        return _login_page(request, next_path, LOGIN_ERRORS[401], status_code=401)
    except OrchestratorError as error:
        message = LOGIN_ERRORS.get(error.status_code) or error_markup(error.code, error.message)
        return _login_page(request, next_path, message, status_code=error.status_code)
    except OrchestratorUnavailableError:
        return _login_page(request, next_path, UNAVAILABLE, status_code=502)
    response = RedirectResponse(next_path, status_code=303)
    set_session_cookie(
        response, session.session_token, request.app.state.settings.session_cookie_secure
    )
    return response


@router.post("/review/logout", dependencies=[Depends(read_form)])  # the same-site check
def logout(request: Request) -> Response:
    # Already ended, or the orchestrator is down: the cookie goes anyway.
    with contextlib.suppress(OrchestratorError, OrchestratorUnavailableError):
        orchestrator_for(request).logout()
    response = RedirectResponse(LOGIN_PATH, status_code=303)
    clear_session_cookie(response, request.app.state.settings.session_cookie_secure)
    return response
