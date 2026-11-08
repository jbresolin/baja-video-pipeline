"""The overlay preview's look: BajaOvrLay's tokens.css and Bebas Neue (LIVESTACK-48).

The job page draws the clip's overlay over the proxy in HTML/CSS, with the
very files the worker draws the clip with: overlay_theme/ holds byte-for-
byte copies of worker/src/worker/overlay_theme/'s tokens.css, font and
license (tools/refresh_overlay_theme.py writes both; a test keeps them
equal). The review UI image does not contain the worker, hence the copy.

tokens.css declares its custom properties on `:root`, and some names
(--radius, --text) mean something else in the review UI's own app.css.
So it is served *scoped*: the one `:root` rule becomes `.overlay-preview`,
the preview's own element, and nothing else on the page changes. A
@font-face for the vendored font is added, under the family name the
tokens ask for ('Bebas Neue').
"""

import re
from functools import cache
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse, Response

from review_ui import __version__

THEME_DIR = Path(__file__).parent / "overlay_theme"
TOKENS_FILE = "tokens.css"
FONT_FILE = "BebasNeue-Regular.ttf"
URL_PREFIX = "/review/overlay-theme"
# The preview's element (templates/job.html); the tokens apply inside it only.
PREVIEW_SELECTOR = ".overlay-preview"
# The rule that opens the declarations: ":root {" at the start of a line
# (the word also appears in a comment, which must stay as it is).
_ROOT_RULE = re.compile(r"^:root(\s*\{)", re.MULTILINE)
# Browsers cache it for a day; the review UI's version in the URL changes on deploy.
CACHE_SECONDS = 24 * 3600

router = APIRouter(prefix=URL_PREFIX)


class ThemeScopeError(ValueError):
    """tokens.css changed shape: not exactly one `:root {` rule to scope."""


def scoped_tokens_css(css_text: str) -> str:
    """tokens.css with its `:root` rule applying to the preview only, plus the font."""
    scoped, count = _ROOT_RULE.subn(PREVIEW_SELECTOR + r"\1", css_text)
    if count != 1:
        raise ThemeScopeError(f"expected one ':root {{' rule in tokens.css, found {count}")
    font_face = (
        "@font-face {\n"
        "  font-family: 'Bebas Neue';\n"
        f"  src: url('{URL_PREFIX}/{FONT_FILE}') format('truetype');\n"
        "  font-weight: 400;\n"
        "  font-display: block;\n"
        "}\n"
    )
    return font_face + scoped


@cache
def _served_tokens_css() -> str:
    return scoped_tokens_css((THEME_DIR / TOKENS_FILE).read_text(encoding="utf-8"))


def overlay_theme_url(file_name: str) -> str:
    """The URL of a theme file, versioned like the static files."""
    return f"{URL_PREFIX}/{file_name}?v={__version__}"


@router.get(f"/{TOKENS_FILE}")
def tokens_css() -> Response:
    return Response(
        _served_tokens_css(),
        media_type="text/css",
        headers={"Cache-Control": f"public, max-age={CACHE_SECONDS}"},
    )


@router.get(f"/{FONT_FILE}")
def font_file() -> FileResponse:
    return FileResponse(
        THEME_DIR / FONT_FILE,
        media_type="font/ttf",
        headers={"Cache-Control": f"public, max-age={CACHE_SECONDS}"},
    )
