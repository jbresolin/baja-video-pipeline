"""Tests that /srv/pipeline/reviewers.txt takes effect without a restart.

On event day João adds a reviewer with `echo "name hash" >> reviewers.txt`
and reloads Caddy (infra/vps/README.md §4). The orchestrator checks the
same file itself, so it must notice the change on its own, the same way
it notices edits to teams.csv: the file is re-read whenever its
modification time or size changes. Each test edits the real file under a
running orchestrator and then makes an ordinary request.
"""

import bcrypt

from orchestrator.testing.harness import REVIEWER_NAME, REVIEWER_PASSWORD


def reviewer_line(username: str, password: str) -> str:
    """One `username bcrypt-hash` line; cost 4 keeps the test fast."""
    password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=4)).decode()
    return f"{username} {password_hash}\n"


def queue_status(harness, username: str, password: str) -> int:
    """Status of a reviewer-only request: 200 = logged in, 401 = refused."""
    return harness.client.get("/review/queue", auth=(username, password)).status_code


def test_a_reviewer_added_to_the_file_can_log_in_on_the_next_request(harness):
    reviewers_file = harness.context.settings.reviewers_file
    # Before: "bruno" is not in the file, so he is refused.
    assert queue_status(harness, "bruno", "senha-do-bruno") == 401

    # João appends Bruno's line to the file, as the runbook says (no restart).
    with reviewers_file.open("a", encoding="utf-8") as file:
        file.write(reviewer_line("bruno", "senha-do-bruno"))

    # The very next request authenticates him, and Ana still works too.
    assert queue_status(harness, "bruno", "senha-do-bruno") == 200
    assert queue_status(harness, REVIEWER_NAME, REVIEWER_PASSWORD) == 200


def test_a_reviewer_removed_from_the_file_is_refused_even_after_logging_in(harness):
    reviewers_file = harness.context.settings.reviewers_file
    # Ana logs in once; the orchestrator remembers her password check so
    # it does not pay bcrypt on every request.
    assert queue_status(harness, REVIEWER_NAME, REVIEWER_PASSWORD) == 200

    # Her line is removed (only Bruno stays).
    reviewers_file.write_text(reviewer_line("bruno", "x"), encoding="utf-8")

    # That remembered check must not outlive the file: she is refused now.
    assert queue_status(harness, REVIEWER_NAME, REVIEWER_PASSWORD) == 401


def test_a_half_edited_file_keeps_the_previous_reviewers(harness):
    reviewers_file = harness.context.settings.reviewers_file
    # Someone pastes an htpasswd-style "user:hash" line by mistake. The
    # file no longer parses...
    with reviewers_file.open("a", encoding="utf-8") as file:
        file.write("carla:$2y$04$notreallyahash\n")

    # ...so the orchestrator keeps the last good version instead of
    # locking every reviewer out in the middle of the event.
    assert queue_status(harness, REVIEWER_NAME, REVIEWER_PASSWORD) == 200
    assert queue_status(harness, "carla", "anything") == 401


def test_a_deleted_file_means_no_reviewers(harness):
    # Deleting the file is a way to revoke everyone at once.
    harness.context.settings.reviewers_file.unlink()
    assert queue_status(harness, REVIEWER_NAME, REVIEWER_PASSWORD) == 401
