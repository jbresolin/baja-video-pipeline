"""A sender's videos still waiting for review without a prova (LIVESTACK-51 item 2).

Intake fills a contact's default prova into each *new* video. A default set
(or changed) later reaches no video that already arrived, so the admin may
also apply it to that sender's videos still waiting for review that have
no prova yet: received, proxy_ready or awaiting_review, `prova` empty. A
prova a reviewer already chose is never replaced, and a video already
reviewed is left alone.

Jobs are matched to the contact by their sender JID through the same phone
rule as contacts (domain/phones.py), so a JID without the 9th digit counts.
Filling the prova is not a reviewer's change: the job's review version
stays (docs/api.md §4), so a reviewer with the page open is not refused
on save; the page sends the prova only when its field has one.
"""

import sqlite3
from collections import Counter
from datetime import datetime

from orchestrator.domain.contacts import sender_phone
from orchestrator.repositories import events as events_repo
from orchestrator.repositories import jobs as jobs_repo
from shared.models import JobState

# Not reviewed yet: a reviewer has not chosen anything for these.
WAITING_STATES = (JobState.RECEIVED, JobState.PROXY_READY, JobState.AWAITING_REVIEW)


def _waiting_rows_without_prova(connection: sqlite3.Connection) -> list[sqlite3.Row]:
    placeholders = ", ".join("?" for _ in WAITING_STATES)
    return connection.execute(
        f"SELECT id, sender_jid FROM jobs WHERE (prova IS NULL OR prova = '')"
        f" AND state IN ({placeholders}) ORDER BY created_at, id",
        [str(state) for state in WAITING_STATES],
    ).fetchall()


def waiting_without_prova_counts(connection: sqlite3.Connection) -> Counter[str]:
    """Contact phone → how many of their waiting videos have no prova."""
    return Counter(
        phone
        for row in _waiting_rows_without_prova(connection)
        if (phone := sender_phone(row["sender_jid"])) is not None
    )


def apply_to_waiting(
    connection: sqlite3.Connection, phone: str, prova_name: str, now: datetime, actor: str
) -> list[str]:
    """Fill `prova_name` into the contact's waiting videos without a prova; their ids."""
    filled = []
    for row in _waiting_rows_without_prova(connection):
        if sender_phone(row["sender_jid"]) != phone:
            continue
        jobs_repo.update_job(connection, row["id"], now, {"prova": prova_name})
        events_repo.record_event(
            connection,
            now,
            actor=actor,
            kind="default_prova_applied",
            job_id=row["id"],
            reason=f"the sender's default prova {prova_name!r}, applied by an admin",
        )
        filled.append(row["id"])
    return filled
