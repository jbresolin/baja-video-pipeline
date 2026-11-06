"""Static files of the localhost page (http://127.0.0.1:8700): page.js.

A package so the frozen build bundles it (installer/build.py DATA_PACKAGES)
and so the code reads it with importlib.resources.
"""

from importlib import resources


def page_script() -> str:
    """The text of page.js, inlined into every page."""
    return resources.files(__name__).joinpath("page.js").read_text(encoding="utf-8")
