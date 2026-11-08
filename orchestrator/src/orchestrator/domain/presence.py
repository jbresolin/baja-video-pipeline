"""Who has a job page open (LIVESTACK-51 item 1).

On the 2026-11-07 test two people opened the same video and both worked on
it; the version check (docs/api.md §4) only stopped the second *save*. Now
the job page tells the orchestrator every ~20 s that its reviewer is on it
(PUT /jobs/{id}/presence), and says goodbye when it closes (DELETE). The
answer lists the *other* reviewers on that job, so the page can warn; the
queue shows, on each row, who is on it.

**Nothing is ever locked.** This only informs: a reviewer can always go on
("Revisar mesmo assim"), and an entry the page stopped refreshing expires
after 45 s, so a closed laptop, a crashed browser or a lost connection never
keeps a video away from the others.

**In memory only.** Presence is worth nothing after a restart (the pages
ping again within 20 s), so it is not stored. One process runs the
orchestrator (design §17), so one board serves every request.

A user with the same video open in two tabs is one entry: the reviewer is
the signed-in username (LIVESTACK-37 users), the time "desde" is when that
user's first ping arrived.
"""

import threading
from dataclasses import dataclass
from datetime import datetime, timedelta

from shared.models import PresentReviewer

# Without a ping for this long, a reviewer is no longer on the job. The
# page pings every 20 s, so two lost pings are tolerated.
PRESENCE_EXPIRES_AFTER = timedelta(seconds=45)


@dataclass
class _Visit:
    since: datetime
    last_ping: datetime


class PresenceBoard:
    """job id → username → when they arrived and when they last pinged."""

    def __init__(self) -> None:
        self._visits: dict[str, dict[str, _Visit]] = {}
        self._lock = threading.Lock()

    def _drop_expired(self, now: datetime) -> None:
        for job_id in list(self._visits):
            visits = self._visits[job_id]
            for username in [
                name
                for name, visit in visits.items()
                if now - visit.last_ping >= PRESENCE_EXPIRES_AFTER
            ]:
                del visits[username]
            if not visits:
                del self._visits[job_id]

    @staticmethod
    def _listed(visits: dict[str, _Visit], leave_out: str | None) -> list[PresentReviewer]:
        """Everyone but `leave_out`, who arrived first first."""
        present = [
            PresentReviewer(username=username, since=visit.since)
            for username, visit in visits.items()
            if username != leave_out
        ]
        return sorted(present, key=lambda reviewer: (reviewer.since, reviewer.username))

    def ping(self, job_id: str, username: str, now: datetime) -> list[PresentReviewer]:
        """`username` is on `job_id` now. Returns the *other* reviewers on it."""
        with self._lock:
            self._drop_expired(now)
            visits = self._visits.setdefault(job_id, {})
            visit = visits.get(username)
            if visit is None:
                visits[username] = _Visit(since=now, last_ping=now)
            else:
                visit.last_ping = now
            return self._listed(visits, leave_out=username)

    def leave(self, job_id: str, username: str) -> None:
        """The page closed (or moved on): `username` is no longer on `job_id`."""
        with self._lock:
            visits = self._visits.get(job_id, {})
            visits.pop(username, None)
            if not visits:
                self._visits.pop(job_id, None)

    def everyone(self, now: datetime) -> dict[str, list[PresentReviewer]]:
        """job id → every reviewer on it, for the queue's "👤" marks."""
        with self._lock:
            self._drop_expired(now)
            return {
                job_id: self._listed(visits, leave_out=None)
                for job_id, visits in self._visits.items()
            }
