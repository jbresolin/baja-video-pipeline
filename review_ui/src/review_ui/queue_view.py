"""Filters for the queue page (GET /review/).

The orchestrator filters by state. Prova and the "flagged" (low_res) filter
are applied here, on the list it returned.
"""

from dataclasses import dataclass, field
from urllib.parse import urlencode

from shared.layout import slugify_prova
from shared.models import JobState, JobSummary

# What the queue shows when no state is chosen: work that needs a human.
DEFAULT_QUEUE_STATES = [JobState.AWAITING_REVIEW, JobState.FAILED]

# Every state, in the order of the pipeline, for the filter checkboxes.
STATES_IN_PIPELINE_ORDER = [
    JobState.RECEIVED,
    JobState.PROXY_READY,
    JobState.AWAITING_REVIEW,
    JobState.READY,
    JobState.CLAIMED,
    JobState.UPLOADED,
    JobState.DELIVERED,
    JobState.DONE,
    JobState.FAILED,
    JobState.DISCARDED,
]


@dataclass(frozen=True)
class QueueFilter:
    states: list[JobState] = field(default_factory=lambda: list(DEFAULT_QUEUE_STATES))
    # Empty string = any prova.
    prova: str = ""
    # Only jobs flagged low_res (arrived as a recompressed WhatsApp video).
    flagged_only: bool = False

    def query_string(self) -> str:
        """The same filter as URL parameters, for links and the polling URL."""
        params: list[tuple[str, str]] = [("state", state.value) for state in self.states]
        if self.prova:
            params.append(("prova", self.prova))
        if self.flagged_only:
            params.append(("flagged", "1"))
        return urlencode(params)


def parse_queue_filter(states: list[str], prova: str | None, flagged: str | None) -> QueueFilter:
    """Build a QueueFilter from query parameters, ignoring unknown state names.

    A bad link (e.g. ?state=nonsense) then shows the default queue instead
    of an error page.
    """
    known_values = {state.value for state in JobState}
    chosen = [JobState(value) for value in states if value in known_values]
    # Keep pipeline order and drop duplicates, so the URL is stable.
    ordered = [state for state in STATES_IN_PIPELINE_ORDER if state in chosen]
    return QueueFilter(
        states=ordered or list(DEFAULT_QUEUE_STATES),
        prova=(prova or "").strip(),
        flagged_only=flagged in {"1", "true", "on"},
    )


def same_prova(left: str, right: str) -> bool:
    """Compare provas the way folders are named, so "Suspensão" == "suspensao"."""
    try:
        return slugify_prova(left) == slugify_prova(right)
    except ValueError:
        # A prova with no letters or digits: fall back to plain comparison.
        return left.strip().lower() == right.strip().lower()


def apply_local_filters(items: list[JobSummary], queue_filter: QueueFilter) -> list[JobSummary]:
    kept = items
    if queue_filter.prova:
        kept = [item for item in kept if item.prova and same_prova(item.prova, queue_filter.prova)]
    if queue_filter.flagged_only:
        kept = [item for item in kept if item.low_res]
    return kept


def distinct_provas(provas: list[str | None]) -> list[str]:
    """Unique provas in the order given, comparing them like folder names."""
    unique: list[str] = []
    for prova in provas:
        if prova and not any(same_prova(prova, seen) for seen in unique):
            unique.append(prova)
    return unique
