"""The browser side of signing in: the session cookie, form posts and where to go back to.

The orchestrator issues the session (POST /auth/login). The review UI
keeps its token in a cookie the page's JavaScript cannot read (httpOnly),
that is only sent over HTTPS (Secure) and not on requests started by other
sites (SameSite=Lax). On every request the UI turns the cookie into
`Authorization: Bearer <token>` for the orchestrator. Caddy's forward_auth
sends the same cookie to the orchestrator before serving proxies and
thumbnails, which is why the cookie's path is "/" and not "/review".

Form posts (sign-in, admin pages) are read by forms.py.
"""

from urllib.parse import quote, urlsplit

from fastapi import Request
from fastapi.responses import Response

SESSION_COOKIE_NAME = "bvp_session"
LOGIN_PATH = "/review/login"
HOME_PATH = "/review/"


def session_token(request: Request) -> str | None:
    return request.cookies.get(SESSION_COOKIE_NAME) or None


def set_session_cookie(response: Response, token: str, secure: bool) -> None:
    # No max_age: the browser keeps it for the browsing session, and the
    # orchestrator ends the session itself after 12 h without requests.
    response.set_cookie(
        SESSION_COOKIE_NAME, token, path="/", httponly=True, secure=secure, samesite="lax"
    )


def clear_session_cookie(response: Response, secure: bool) -> None:
    response.delete_cookie(
        SESSION_COOKIE_NAME, path="/", httponly=True, secure=secure, samesite="lax"
    )


def safe_next_path(candidate: str | None) -> str:
    """Where to go after signing in: only a path inside the review UI.

    Anything else (another site, "//evil.example", a missing value) goes
    to the queue, so a crafted login link cannot send people elsewhere.
    """
    if not candidate or not candidate.startswith("/review") or candidate.startswith("//"):
        return HOME_PATH
    parts = urlsplit(candidate)
    if parts.scheme or parts.netloc or parts.path.startswith(LOGIN_PATH):
        return HOME_PATH
    return candidate


def login_redirect_target(request: Request) -> str:
    """The login page, remembering the page the browser asked for."""
    wanted = request.url.path + (f"?{request.url.query}" if request.url.query else "")
    return f"{LOGIN_PATH}?next={quote(safe_next_path(wanted), safe='/')}"
