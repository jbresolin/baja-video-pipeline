"""Where the orchestrator gets "now" from.

Leases, rate limits and the background sweepers all depend on time. Code
asks a Clock instead of calling datetime.now() directly, so tests can use
a ManualClock and move time forward by exactly 46 seconds to see a lease
expire, without sleeping.
"""

from datetime import UTC, datetime, timedelta


class Clock:
    """The real clock: always UTC, never naive."""

    def now(self) -> datetime:
        return datetime.now(UTC)


class ManualClock(Clock):
    """A clock that only moves when told to. For tests and the fakes."""

    def __init__(self, start: datetime | None = None) -> None:
        self._now = start or datetime(2026, 10, 25, 12, 0, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self._now

    def advance(self, seconds: float) -> datetime:
        self._now += timedelta(seconds=seconds)
        return self._now
