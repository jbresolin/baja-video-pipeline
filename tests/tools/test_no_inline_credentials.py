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

The installer's CI scripts (installer/ci/*.ps1) are checked the same way
in PowerShell's shapes: a password they need is generated at run time
(smoke_install.ps1's New-ThrowawayUserPassword), never typed in.
"""

import re
from pathlib import Path

import pytest

from shared.testing import credentials

REPO_ROOT = Path(__file__).resolve().parents[2]
TESTS_FOLDER = REPO_ROOT / "tests"
INSTALLER_CI_FOLDER = REPO_ROOT / "installer" / "ci"
CREDENTIALS_MODULE = REPO_ROOT / "shared" / "src" / "shared" / "testing" / "credentials.py"

# A word ending in "password" (password, new_password, PLACEHOLDER_PASSWORD...),
# then = or a closing quote and a colon, then an opening quote.
INLINE_PASSWORD = re.compile(
    r"""(?ix)
    (?: \b\w*password \s* = \s* | ["']\w*password["'] \s* : \s* )
    [rbfu]? ["']
    """
)


# PowerShell: a variable or parameter whose name ends in "password", or
# ConvertTo-SecureString, followed by a quoted string:
#     $password = "..."     -Password '...'     ConvertTo-SecureString "..."
POWERSHELL_INLINE_PASSWORD = re.compile(
    r"""(?ix)
    (?: \$\w*password \s* = \s* | -\w*password \s+ | ConvertTo-SecureString \s+ (?:-String \s+)? )
    ["']
    """
)


def inline_passwords(text: str, pattern: re.Pattern = INLINE_PASSWORD) -> list[str]:
    """The lines of `text` that contain a password string literal."""
    return [line.strip() for line in text.splitlines() if pattern.search(line)]


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


def test_the_powershell_check_catches_literals_and_allows_generated_values():
    # The guard for the guard, PowerShell edition. The first line is the
    # shape smoke_install.ps1 had before (a fixed prefix is a literal too).
    flagged = (
        '$password = "Tr4y-" + [guid]::NewGuid()\n'
        "New-LocalUser bajatray -Password 'abc'\n"
        'ConvertTo-SecureString "abc" -AsPlainText -Force'
    )
    assert len(inline_passwords(flagged, POWERSHELL_INLINE_PASSWORD)) == 3
    allowed = (
        "$throwawayUserPassword = New-ThrowawayUserPassword\n"
        "ConvertTo-SecureString $throwawayUserPassword -AsPlainText -Force"
    )
    assert inline_passwords(allowed, POWERSHELL_INLINE_PASSWORD) == []


INSTALLER_CI_SCRIPTS = sorted(INSTALLER_CI_FOLDER.glob("*.ps1"))


def test_there_are_installer_ci_scripts_to_check():
    # If the scripts move, this fails instead of the check below quietly
    # checking nothing.
    assert INSTALLER_CI_SCRIPTS


@pytest.mark.parametrize(
    "path", INSTALLER_CI_SCRIPTS, ids=lambda path: str(path.relative_to(REPO_ROOT))
)
def test_no_password_literals_in_installer_ci_scripts(path):
    offending = inline_passwords(path.read_text(encoding="utf-8"), POWERSHELL_INLINE_PASSWORD)
    assert offending == [], (
        f"{path.relative_to(REPO_ROOT)}: generate the password at run time instead of: {offending}"
    )


def test_the_credentials_module_holds_obvious_placeholders_only():
    # Every password/token constant there says it is a placeholder (or is
    # the deliberately too-short one), so nobody mistakes it for real.
    for name in dir(credentials):
        if name.startswith("PLACEHOLDER_") and name != "PLACEHOLDER_SHORT_PASSWORD":
            assert "placeholder" in getattr(credentials, name), name
    assert CREDENTIALS_MODULE.is_file()
