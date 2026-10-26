"""Read the reviewers file (BVP_REVIEWERS_FILE) shared by Caddy and the orchestrator.

Format (design §15), e.g. /srv/pipeline/reviewers.txt:

    # Baja review team
    ana $2a$14$Zkx19XLiW6VYouLHR5NmfOFU0z2GTNmpkT/5qqR7hx4IjWJPDhjvG
    bruno $2a$14$...

* one `username bcrypt-hash` pair per line, separated by ONE space;
* a line whose first non-blank character is `#` is a comment;
* blank lines are ignored; whitespace at either end of a line is ignored.

This is Caddy's `basic_auth` syntax, so the Caddyfile can simply
`import` the file. Real htpasswd files use `user:hash`, which Caddy cannot
read, so that form is refused with a message saying so. Make a hash with
`caddy hash-password` (gives $2a$) or `htpasswd -nbB` (gives $2y$; keep
only the part after the colon).

This module only parses; checking a password against a hash (bcrypt) is
the orchestrator's job.
"""

import re
from pathlib import Path

DEFAULT_REVIEWERS_FILE = Path("/srv/pipeline/reviewers.txt")

# $2a$, $2b$ or $2y$, a two-digit cost, then 53 characters of salt + hash.
BCRYPT_HASH_PATTERN = re.compile(r"^\$2[aby]\$\d{2}\$[./A-Za-z0-9]{53}$")
# Letters, digits and . _ @ - only: no spaces, colons or '#'.
USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9._@-]+$")


class ReviewersFileError(ValueError):
    """A line of the reviewers file is wrong. The message names the line."""

    def __init__(self, line_number: int, problem: str) -> None:
        self.line_number = line_number
        super().__init__(f"reviewers file, line {line_number}: {problem}")


def parse_reviewers(text: str) -> dict[str, str]:
    """Return {username: bcrypt hash} from the file's text, or raise ReviewersFileError."""
    reviewers: dict[str, str] = {}
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        username, password_hash = _split_line(line, line_number)
        if username in reviewers:
            raise ReviewersFileError(line_number, f"reviewer '{username}' is listed twice")
        reviewers[username] = password_hash
    return reviewers


def load_reviewers_file(path: Path = DEFAULT_REVIEWERS_FILE) -> dict[str, str]:
    return parse_reviewers(path.read_text(encoding="utf-8"))


def _split_line(line: str, line_number: int) -> tuple[str, str]:
    parts = line.split(" ")
    if len(parts) == 1 and ":" in line:
        raise ReviewersFileError(
            line_number,
            "looks like htpasswd 'user:hash'; write 'user hash' (one space) so Caddy can read it",
        )
    if len(parts) != 2 or "\t" in line:
        raise ReviewersFileError(
            line_number, "expected exactly 'username bcrypt-hash' separated by one space"
        )
    username, password_hash = parts
    if not USERNAME_PATTERN.match(username):
        raise ReviewersFileError(
            line_number, f"username {username!r} may only use letters, digits and . _ @ -"
        )
    if not BCRYPT_HASH_PATTERN.match(password_hash):
        raise ReviewersFileError(
            line_number, f"the password for '{username}' is not a bcrypt hash ($2a$/$2b$/$2y$)"
        )
    return username, password_hash
