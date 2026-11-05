"""Reading the plain HTML forms of the sign-in and admin pages.

SameSite=Lax already keeps the session cookie off form posts that another
site's page sends. As a second line, every form post must also say it
comes from this site (Sec-Fetch-Site / Origin), or it is refused.
"""

from urllib.parse import parse_qs, urlsplit

from fastapi import Request

from review_ui.catalog import text
from review_ui.errors import UiRequestError


def check_same_origin(request: Request) -> None:
    """Refuse a form post that another site's page sent (cross-site request forgery)."""
    if request.headers.get("sec-fetch-site", "same-origin") not in ("same-origin", "none"):
        raise UiRequestError(403, "forbidden", text("error.form_from_other_site"))
    origin = request.headers.get("origin")
    if origin and urlsplit(origin).netloc != request.headers.get("host"):
        raise UiRequestError(403, "forbidden", text("error.form_from_other_site"))


async def read_form(request: Request) -> dict[str, str]:
    """The fields of an urlencoded form post (last value wins), after the origin check."""
    check_same_origin(request)
    content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if content_type != "application/x-www-form-urlencoded":
        raise UiRequestError(415, "validation_error", text("error.send_as_form"))
    body = (await request.body()).decode("utf-8", errors="replace")
    return {name: values[-1] for name, values in parse_qs(body, keep_blank_values=True).items()}
