"""Tests for shared.reviewers_file: the list of reviewers and their password hashes.

Background for a beginner: the review UI asks for a username and password.
We never store passwords; we store a bcrypt *hash* of each one (a scrambled
form that can check a password but not reveal it). Caddy and the
orchestrator both read the same file of `username hash` lines. These tests
check the parser accepts exactly that format and explains every mistake.
"""

import pytest

from shared.reviewers_file import (
    DEFAULT_REVIEWERS_FILE,
    ReviewersFileError,
    load_reviewers_file,
    parse_reviewers,
)

# Two well-formed bcrypt hashes (made with `caddy hash-password` and
# `htpasswd -nbB`). Their exact content does not matter to the parser,
# only their shape.
HASH_A = "$2a$14$Zkx19XLiW6VYouLHR5NmfOFU0z2GTNmpkT/5qqR7hx4IjWJPDhjvG"
HASH_Y = "$2y$05$GSNSWdsjEMHlNpC4bm6t1.u9AYqnyN9YhH8lkHrQV8.mcbv7JJ1Wa"


def test_valid_file_with_comments_and_blank_lines():
    text = f"""# Baja review team

ana {HASH_A}
   # an indented comment is still a comment
bruno {HASH_Y}

"""
    # Comments and empty lines are skipped; each real line becomes one entry.
    assert parse_reviewers(text) == {"ana": HASH_A, "bruno": HASH_Y}


def test_trailing_whitespace_and_windows_line_endings_are_ignored():
    # Editors often leave spaces at the end of a line, and Windows ends
    # lines with "\r\n". Neither may become part of the hash, or no
    # password would ever match.
    text = f"ana {HASH_A}   \r\nbruno {HASH_Y}\t\r\n"
    assert parse_reviewers(text) == {"ana": HASH_A, "bruno": HASH_Y}


def test_empty_file_means_no_reviewers():
    assert parse_reviewers("") == {}
    assert parse_reviewers("# nobody yet\n\n") == {}


def test_htpasswd_colon_format_is_refused_with_a_helpful_message():
    # Real htpasswd writes "user:hash". Caddy cannot import that, so we
    # refuse it too, and the message says exactly how to fix it.
    with pytest.raises(ReviewersFileError) as caught:
        parse_reviewers(f"ana:{HASH_A}\n")
    assert caught.value.line_number == 1
    assert "user hash" in str(caught.value)


@pytest.mark.parametrize(
    ("bad_line", "expected_words"),
    [
        # Two spaces, a tab, or extra words: "one space" is the rule.
        (f"ana  {HASH_A}", "one space"),
        (f"ana\t{HASH_A}", "one space"),
        (f"ana {HASH_A} # admin", "one space"),
        # A username alone, with no hash.
        ("ana", "one space"),
        # A plain-text password instead of a hash must never be accepted.
        ("ana secret123", "not a bcrypt hash"),
        # Characters that would confuse Caddy or Basic auth.
        (f"ana#1 {HASH_A}", "letters, digits"),
    ],
)
def test_malformed_lines_raise_an_error_naming_the_line(bad_line, expected_words):
    # The bad line is line 3 (after a comment and a good line), so the
    # error must say "line 3": that is how the admin finds it.
    text = f"# team\nbruno {HASH_Y}\n{bad_line}\n"
    with pytest.raises(ReviewersFileError) as caught:
        parse_reviewers(text)
    assert caught.value.line_number == 3
    assert "line 3" in str(caught.value)
    assert expected_words in str(caught.value)


def test_the_same_reviewer_twice_is_an_error():
    # Which password would win? Refuse instead of guessing.
    with pytest.raises(ReviewersFileError, match="listed twice"):
        parse_reviewers(f"ana {HASH_A}\nana {HASH_Y}\n")


def test_load_reads_the_file_from_disk(tmp_path):
    reviewers_file = tmp_path / "reviewers.txt"
    reviewers_file.write_text(f"ana {HASH_A}\n", encoding="utf-8")
    assert load_reviewers_file(reviewers_file) == {"ana": HASH_A}
    # The default location agreed with the manager (design §15).
    assert str(DEFAULT_REVIEWERS_FILE) == "/srv/pipeline/reviewers.txt"
