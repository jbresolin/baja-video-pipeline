"""Tests must take their passwords from shared/testing/credentials.py (design §14).

Why: a password typed straight into a test ("password": "s3cret") looks
like a leaked credential to people and to secret scanners (GitGuardian
flagged this repo's tests). All test credentials are obvious placeholders
in one module, which .gitguardian.yaml tells ggshield to ignore.

How: this test reads every Python file under tests/ and looks for a
password given as a quoted string right there, in the two shapes that
appear in tests:

    password="..."          a keyword argument or an assignment
    "password": "..."       a JSON body / dict entry

`password=PLACEHOLDER_PASSWORD` or `"password": password` are fine: the
value is a name, not a string literal.
"""

import re
from pathlib import Path

import pytest

from shared.testing import credentials

REPO_ROOT = Path(__file__).resolve().parents[2]
TESTS_FOLDER = REPO_ROOT / "tests"
CREDENTIALS_MODULE = REPO_ROOT / "shared" / "src" / "shared" / "testing" / "credentials.py"

# A word ending in "password" (password, new_password, PLACEHOLDER_PASSWORD...),
# then = or a closing quote and a colon, then an opening quote.
INLINE_PASSWORD = re.compile(
    r"""(?ix)
    (?: \b\w*password \s* = \s* | ["']\w*password["'] \s* : \s* )
    [rbfu]? ["']
    """
)


def inline_passwords(text: str) -> list[str]:
    """The lines of `text` that contain a password string literal."""
    return [line.strip() for line in text.splitlines() if INLINE_PASSWORD.search(line)]


def test_the_check_catches_both_shapes_and_allows_names():
    # A guard for the guard: it must flag literals and leave names alone.
    flagged = 'login(password="abc")\nbody = {"password": "abc"}\nNEW_PASSWORD = \'x\''
    assert len(inline_passwords(flagged)) == 3
    allowed = 'login(password=PLACEHOLDER_PASSWORD)\nbody = {"password": password}'
    assert inline_passwords(allowed) == []


# This file is the one exception: it has to spell out the patterns it bans.
SCANNED_FILES = sorted(
    path for path in TESTS_FOLDER.rglob("*.py") if path != Path(__file__).resolve()
)


@pytest.mark.parametrize("path", SCANNED_FILES, ids=lambda path: str(path.relative_to(REPO_ROOT)))
def test_no_password_literals_under_tests(path):
    offending = inline_passwords(path.read_text(encoding="utf-8"))
    assert offending == [], (
        f"{path.relative_to(REPO_ROOT)}: use a placeholder from "
        f"shared/testing/credentials.py instead of: {offending}"
    )


def test_the_credentials_module_holds_obvious_placeholders_only():
    # Every password/token constant there says it is a placeholder (or is
    # the deliberately too-short one), so nobody mistakes it for real.
    for name in dir(credentials):
        if name.startswith("PLACEHOLDER_") and name != "PLACEHOLDER_SHORT_PASSWORD":
            assert "placeholder" in getattr(credentials, name), name
    assert CREDENTIALS_MODULE.is_file()
