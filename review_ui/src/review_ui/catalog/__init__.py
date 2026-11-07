"""Every text the review UI shows to a person, in pt-BR, in one catalog (LIVESTACK-39).

The reviewers are Brazilian volunteers, so the whole UI speaks pt-BR. No
sentence meant for a person is written inline in a template, a view or a
script: they all live in the modules of this package, under English keys
so the code stays readable ("queue.title", "state.awaiting_review").

* Python views call `text(key, **values)` and get a plain string.
* Templates call `t(key, **values)` (a Jinja global). Catalog entries may
  contain a little HTML (<kbd>, <em>, <code>, links), so `t` returns Markup;
  the values filled in are escaped, so a sender's name can never become HTML.
* `text_plural` / `tn` pick "<key>.one" when the count is 1, else "<key>.other".
* The page scripts get the keys that start with "script." as JSON embedded
  in every page (base.html), read by static/js/text.js. Same `{name}`
  placeholders on both sides.

The modules are split by area only to keep each file short; together they
are one catalog, and a key may be defined only once (checked at import).
"""

from markupsafe import Markup

from review_ui.catalog import (
    admin_pages,
    admin_settings,
    clips_and_fleet,
    common,
    job,
    passagens,
    scripts,
    welcome,
)


def _merge(*parts: dict[str, str]) -> dict[str, str]:
    merged: dict[str, str] = {}
    for part in parts:
        repeated = merged.keys() & part.keys()
        if repeated:
            raise ValueError(f"catalog keys defined twice: {sorted(repeated)}")
        merged.update(part)
    return merged


CATALOG: dict[str, str] = _merge(
    common.TEXT,
    job.TEXT,
    passagens.TEXT,
    clips_and_fleet.TEXT,
    admin_pages.TEXT,
    admin_settings.TEXT,
    welcome.TEXT,
    scripts.TEXT,
)

# What the page scripts can use (embedded as JSON in base.html).
SCRIPT_PREFIX = "script."
SCRIPT_TEXT: dict[str, str] = {
    key: value for key, value in CATALOG.items() if key.startswith(SCRIPT_PREFIX)
}


def has_text(key: str) -> bool:
    return key in CATALOG


def text(key: str, **values: object) -> str:
    """The catalog entry for `key`, with its {placeholders} filled in.

    A missing key raises KeyError on purpose: a typo must fail the tests,
    not show a key name to the reviewers.
    """
    entry = CATALOG[key]
    return entry.format(**values) if values else entry


def plural_key(key: str, count: int) -> str:
    return f"{key}.one" if count == 1 else f"{key}.other"


def text_plural(key: str, count: int, **values: object) -> str:
    """ "1 vídeo" / "3 vídeos": the .one or .other entry, with {count} filled in."""
    return text(plural_key(key, count), count=count, **values)


def markup(key: str, **values: object) -> Markup:
    """For templates: the entry as trusted HTML, with the values escaped."""
    entry = Markup(CATALOG[key])
    return entry.format(**values) if values else entry


def markup_plural(key: str, count: int, **values: object) -> Markup:
    return markup(plural_key(key, count), count=count, **values)
