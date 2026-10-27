"""The Jinja2 setup: template folder, filters and helper functions."""

from functools import partial
from pathlib import Path

from fastapi.templating import Jinja2Templates

from review_ui import __version__, formatting
from review_ui.settings import ReviewUiSettings

PACKAGE_DIR = Path(__file__).parent
TEMPLATES_DIR = PACKAGE_DIR / "templates"
STATIC_DIR = PACKAGE_DIR / "static"
# Where StaticFiles is mounted; every page links its CSS/JS from here.
STATIC_URL_PREFIX = "/review/static"


def static_url(path: str) -> str:
    """Link to a static file. The version in the query makes browsers refetch after a deploy."""
    return f"{STATIC_URL_PREFIX}/{path}?v={__version__}"


def media_url(public_base_url: str, path: str | None) -> str | None:
    """Turn a proxy/thumbnail path from the orchestrator into a link the browser can use.

    Job.proxy_url and Job.thumbnail_url are paths relative to the public
    site (docs/api.md §8.2), e.g. "/proxies/abc.mp4", served by Caddy.
    """
    if not path:
        return None
    if path.startswith("/") and public_base_url:
        return public_base_url + path
    return path


def build_templates(settings: ReviewUiSettings) -> Jinja2Templates:
    # Starlette turns autoescaping on, so sender names and worker messages
    # can never inject HTML into the page.
    templates = Jinja2Templates(directory=TEMPLATES_DIR)
    templates.env.filters.update(
        bytes=formatting.format_bytes,
        timecode=formatting.format_timecode,
        duration=formatting.format_duration,
        short_id=formatting.short_id,
        iso=formatting.iso_utc,
        clock=formatting.clock_utc,
        rate=formatting.format_rate,
        state_label=formatting.state_label,
    )
    templates.env.globals.update(
        static=static_url,
        media_url=partial(media_url, settings.public_base_url),
        percent=formatting.percent,
        age=formatting.age_text,
    )
    return templates
