"""Contract: sign-in for the review UI and the media session check (admin page, LIVESTACK-37).

Users live in the orchestrator's database (admin Users page); Caddy no
longer asks for Basic auth. The review UI signs a person in, keeps the
session token in an httpOnly cookie and forwards it as a bearer.
"""

from fastapi import APIRouter, Depends
from fastapi.responses import Response

from shared.models import CurrentUser, LoginRequest, LoginResponse
from tools.openapi_contract.auth import (
    error_responses,
    not_implemented,
    session_bearer,
    session_cookie,
)

router = APIRouter()

_LOGIN_ERRORS = error_responses(400, 401, 429)
_LOGIN_ERRORS[429]["headers"] = {
    "Retry-After": {
        "description": "Seconds until this source IP may try again",
        "schema": {"type": "integer"},
    }
}


@router.post(
    "/auth/login",
    tags=["auth"],
    summary="Sign in with username and password; returns a session token",
    response_model=LoginResponse,
    responses=_LOGIN_ERRORS,
)
def login(body: LoginRequest) -> LoginResponse:
    """No auth header: the password is the credential. Called by the review
    UI, never directly by a browser (Caddy does not route /auth/* publicly).

    * Wrong username, wrong password or a disabled user → 401
      `unauthenticated` (the same answer for all three).
    * At most 20 attempts per source IP per 600 s
      (BVP_LOGIN_RATE_LIMIT_REQUESTS / _WINDOW_S) → 429 `rate_limited` with
      `Retry-After`. The source IP is the last `X-Forwarded-For` hop when
      the caller is a trusted proxy (the review UI passes Caddy's on).
    * The session ends after 12 h without a request (`idle_timeout_s`), at
      logout, or when an admin disables the user or changes their password.
    """
    not_implemented()


@router.post(
    "/auth/logout",
    tags=["auth"],
    summary="End the session",
    status_code=204,
    response_class=Response,
    dependencies=[Depends(session_bearer)],
    responses=error_responses(401),
)
def logout() -> Response:
    not_implemented()


@router.get(
    "/auth/me",
    tags=["auth"],
    summary="Who the session belongs to (username and role)",
    response_model=CurrentUser,
    dependencies=[Depends(session_bearer)],
    responses=error_responses(401),
)
def me() -> CurrentUser:
    """The review UI asks this on every page to show the user's name and,
    for admins, the Admin link."""
    not_implemented()


@router.get(
    "/auth/media",
    tags=["auth"],
    summary="Caddy forward_auth target for /proxies/* and /thumbs/* on the public site",
    status_code=204,
    response_class=Response,
    dependencies=[Depends(session_cookie)],
    responses={
        204: {"description": "A live session: Caddy serves the file"},
        **error_responses(401),
    },
)
def media_access() -> Response:
    """Same pattern as GET /auth/file on the tailnet side. Caddy's
    forward_auth copies the browser's request headers, so the `bvp_session`
    cookie the review UI set arrives here; 2xx allows the file, 401 is
    passed back to the browser. Any signed-in user (reviewer or admin) may
    watch proxies and thumbnails.
    """
    not_implemented()


_REQUESTED_CLIP_RESPONSES = {
    200: {"description": "The whole clip", "content": {"video/mp4": {}}},
    206: {"description": "One byte range (Range: bytes=a-b)", "content": {"video/mp4": {}}},
    **error_responses(401, 404, 416),
}


@router.get(
    "/requested-clips/{job_id}/{file_name}",
    tags=["review"],
    summary='Download a clip a reviewer asked for ("Baixar clipe", LIVESTACK-52)',
    response_class=Response,
    dependencies=[Depends(session_cookie)],
    responses=_REQUESTED_CLIP_RESPONSES,
)
def download_requested_clip(job_id: str, file_name: str) -> Response:
    """Public site; Caddy passes it on unchanged. Unlike proxies, the
    orchestrator sends these bytes itself, because the copy is deleted 30
    minutes after the first *complete* download (the last byte sent; a
    download that breaks off before does not count). Any signed-in user,
    by the `bvp_session` cookie. One Range is honoured. The download is
    named after the clip (Content-Disposition), e.g. suspensao-007-p2.mp4;
    `file_name` in the path is only for the browser."""
    not_implemented()


@router.head(
    "/requested-clips/{job_id}/{file_name}",
    tags=["review"],
    summary="The same answer as GET, without the bytes",
    response_class=Response,
    dependencies=[Depends(session_cookie)],
    responses=_REQUESTED_CLIP_RESPONSES,
)
def requested_clip_headers(job_id: str, file_name: str) -> Response:
    """For download managers asking the size first."""
    not_implemented()
