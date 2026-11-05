"""Routes: sign-in for the review UI, and the session check Caddy asks before serving media.

POST /auth/login    username + password → session token (rate-limited per IP)
POST /auth/logout   ends the session in the Authorization header
GET  /auth/me       who the session belongs to
GET  /auth/media    Caddy forward_auth for /proxies/* and /thumbs/*:
                    204 if the bvp_session cookie is a live session, else 401
"""

from fastapi import APIRouter, Depends, Request, Response

from orchestrator.api.auth import get_context, require_session, session_cookie
from orchestrator.api.rate_limits import enforce_login_rate_limit
from orchestrator.context import OrchestratorContext
from orchestrator.domain import sessions
from orchestrator.domain.sessions import SessionUser
from orchestrator.errors import unauthenticated
from shared.models import CurrentUser, LoginRequest, LoginResponse

router = APIRouter()


@router.post(
    "/auth/login",
    tags=["auth"],
    response_model=LoginResponse,
    dependencies=[Depends(enforce_login_rate_limit)],
)
def login(body: LoginRequest, context: OrchestratorContext = Depends(get_context)) -> LoginResponse:
    return sessions.login(context, body)


@router.post("/auth/logout", tags=["auth"], status_code=204)
def logout(
    user: SessionUser = Depends(require_session),
    context: OrchestratorContext = Depends(get_context),
) -> Response:
    sessions.logout(context, user)
    return Response(status_code=204)


@router.get("/auth/me", tags=["auth"], response_model=CurrentUser)
def me(user: SessionUser = Depends(require_session)) -> CurrentUser:
    return user.as_current_user()


@router.get(
    "/auth/media",
    tags=["auth"],
    status_code=204,
    dependencies=[Depends(session_cookie)],
)
def media_access(request: Request, context: OrchestratorContext = Depends(get_context)) -> Response:
    # forward_auth passes the browser's own headers on, so the cookie is in
    # the raw Cookie header (read by hand: only the session cookie matters).
    token = sessions.session_token_from_cookie_header(request.headers.get("cookie"))
    if token is None or sessions.session_user_for_token(context, token) is None:
        raise unauthenticated("sign in to the review UI to watch proxies")
    return Response(status_code=204)
