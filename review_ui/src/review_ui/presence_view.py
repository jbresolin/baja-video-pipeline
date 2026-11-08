"""Who else is on a video, in words (LIVESTACK-51 item 1).

The orchestrator says which other reviewers have a job page open
(PUT /jobs/{id}/presence); the job page's banner and note and the queue's
"👤" mark say it in pt-BR. Names are the users' sign-in names.
"""

from review_ui.catalog import text
from review_ui.formatting import short_clock_text
from shared.models import PresentReviewer


def names_text(names: list[str]) -> str:
    """ "Alessander", "Alessander e Bruno", "Ana, Alessander e Bruno"."""
    if len(names) <= 1:
        return "".join(names)
    return text("presence.names", first=", ".join(names[:-1]), last=names[-1])


def presence_sentence(others: list[PresentReviewer]) -> str | None:
    """ "Alessander está revisando este vídeo (desde 14:02)."; None when nobody else is.

    With several, the time is when the first of them arrived.
    """
    if not others:
        return None
    key = "presence.one" if len(others) == 1 else "presence.many"
    since = min(reviewer.since for reviewer in others)
    return text(
        key,
        names=names_text([reviewer.username for reviewer in others]),
        since=short_clock_text(since),
    )


def queue_marker(reviewing: list[PresentReviewer], me: str | None) -> str | None:
    """The queue row's "👤 Alessander": everyone on it but the signed-in user."""
    names = [reviewer.username for reviewer in reviewing if reviewer.username != me]
    if not names:
        return None
    return text("presence.queue_marker", names=names_text(names))
