"""Route: GET /requested-clips/<job_id>/<name>, the "Baixar clipe" download (LIVESTACK-52).

Unlike proxies and originals, Caddy does not serve this file itself: it
passes the request on (infra/vps/conf.d/pipeline.caddy), because only the
server sending the bytes can tell that a download finished, and the copy
is deleted 30 minutes after the first one that did
(domain/requested_clips.py). Signed-in users only, by the bvp_session cookie
the browser sends, like GET /auth/media. One byte range is honoured
(`Range: bytes=a-b`), so a broken download can resume.
"""

import re

from fastapi import APIRouter, Depends, Header, Request, Response
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool

from orchestrator.api.auth import get_context, session_cookie
from orchestrator.context import OrchestratorContext
from orchestrator.domain import requested_clips, sessions
from orchestrator.errors import ApiError, unauthenticated
from shared.models import ErrorCode

router = APIRouter()

SINGLE_RANGE = re.compile(r"^bytes=(\d*)-(\d*)$")


def _byte_range(range_header: str | None, size: int) -> tuple[int, int] | None:
    """(first, last) byte for a `Range` header, None for the whole file.

    Only one range is supported; anything else is answered with the whole
    file (allowed by RFC 9110). A range that starts past the end is 416.
    """
    if not range_header:
        return None
    match = SINGLE_RANGE.match(range_header.strip())
    if match is None or (match.group(1) == "" and match.group(2) == ""):
        return None
    start_text, end_text = match.groups()
    if start_text == "":
        # "bytes=-500": the last 500 bytes.
        first = max(size - int(end_text), 0)
        last = size - 1
    else:
        first = int(start_text)
        last = min(int(end_text), size - 1) if end_text else size - 1
    if first >= size or first > last:
        raise ApiError(
            416,
            ErrorCode.VALIDATION_ERROR,
            "range not satisfiable",
            headers={"Content-Range": f"bytes */{size}"},
        )
    return first, last


@router.get(
    "/requested-clips/{job_id}/{file_name}",
    tags=["review"],
    dependencies=[Depends(session_cookie)],
    response_class=Response,
)
async def download_requested_clip(
    job_id: str,
    file_name: str,
    request: Request,
    range_header: str | None = Header(None, alias="Range"),
    context: OrchestratorContext = Depends(get_context),
) -> Response:
    return await _requested_clip_response(context, request, job_id, range_header)


@router.head(
    "/requested-clips/{job_id}/{file_name}",
    tags=["review"],
    dependencies=[Depends(session_cookie)],
    response_class=Response,
)
async def requested_clip_headers(
    job_id: str,
    file_name: str,
    request: Request,
    range_header: str | None = Header(None, alias="Range"),
    context: OrchestratorContext = Depends(get_context),
) -> Response:
    """The same answer without the bytes (a download manager asking the size first)."""
    return await _requested_clip_response(context, request, job_id, range_header)


async def _requested_clip_response(
    context: OrchestratorContext, request: Request, job_id: str, range_header: str | None
) -> Response:
    """`file_name` in the URL only names the browser's download; the clip's
    own readable name goes in Content-Disposition."""
    token = sessions.session_token_from_cookie_header(request.headers.get("cookie"))
    user = None if token is None else sessions.session_user_for_token(context, token)
    if user is None:
        raise unauthenticated("sign in to the review UI to download clips")
    clip = await run_in_threadpool(requested_clips.open_requested_clip, context, job_id)
    byte_range = _byte_range(range_header, clip.size_bytes)
    first, last = byte_range or (0, clip.size_bytes - 1)
    headers = {
        "Accept-Ranges": "bytes",
        "Cache-Control": "private, no-store",
        "Content-Disposition": f'attachment; filename="{clip.file_name}"',
        "Content-Length": str(last - first + 1),
    }
    status_code = 200
    if byte_range is not None:
        status_code = 206
        headers["Content-Range"] = f"bytes {first}-{last}/{clip.size_bytes}"
    if request.method == "HEAD":
        return Response(status_code=status_code, headers=headers, media_type="video/mp4")
    chunks = requested_clips.requested_clip_chunks(
        clip.path,
        first,
        last,
        on_complete=lambda: requested_clips.record_complete_download(
            context, job_id, user.username
        ),
    )
    return StreamingResponse(
        chunks, status_code=status_code, headers=headers, media_type="video/mp4"
    )
