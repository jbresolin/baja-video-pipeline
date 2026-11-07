"""When a video was filmed (Job.recorded_at, LIVESTACK-45).

Arrival order is not filming order: a phone may send three passes at once,
and two filmmakers send in any order. So every job gets a recording time,
from the best source there is:

1. **metadata**: the raw's own `creation_time` (ffprobe's format tags,
   else the first video stream's tags), taken from the probe report the
   first holder sends (PUT /jobs/{id}/media-info). Used only when it is
   plausible: not after the moment the bot received the video, and not
   more than 7 days before it. A phone with a wrong clock, or a file that
   says 1970, fails that test.
2. **whatsapp**: when the filmmaker sent it (the message timestamp), with
   the same plausibility test. Only a hint: the video was filmed before.
3. **received**: when the bot received it.

The job starts with 2 or 3 at intake and moves to 1 when the probe
report arrives. The source is kept, because the review UI shows a send
time as a hint, not as a fact.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

from orchestrator.db.database import to_db_time
from shared.models import ProbeReport, RecordedAtSource

# A recording older than this, relative to the moment the bot received it,
# is not believed (a camera with its clock never set, a 1970 default).
OLDEST_PLAUSIBLE_RECORDING = timedelta(days=7)


def parse_creation_time(value: Any) -> datetime | None:
    """ffprobe's creation_time ("2026-03-26T17:02:11.000000Z") as a UTC datetime.

    A time without a zone is taken as UTC (that is what the MP4 format
    stores). Anything that is not a date and time gives None.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        moment = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)


def creation_time_from_probe(report: ProbeReport | None) -> datetime | None:
    """The raw's creation_time: the container's, else the first video stream's."""
    if report is None:
        return None
    format_tags = report.format.get("tags")
    if isinstance(format_tags, dict):
        moment = parse_creation_time(format_tags.get("creation_time"))
        if moment is not None:
            return moment
    video = next((s for s in report.streams if s.get("codec_type") == "video"), None)
    if video is not None and isinstance(video.get("tags"), dict):
        return parse_creation_time(video["tags"].get("creation_time"))
    return None


def is_plausible(moment: datetime | None, received_at: datetime) -> bool:
    """Not after the bot received the video, and not more than 7 days before."""
    if moment is None:
        return False
    return received_at - OLDEST_PLAUSIBLE_RECORDING <= moment <= received_at


def choose_recorded_at(
    *,
    received_at: datetime,
    sent_at: datetime | None = None,
    probe: ProbeReport | None = None,
) -> tuple[datetime, RecordedAtSource]:
    """(when it was filmed, where that came from), by the rules above."""
    created = creation_time_from_probe(probe)
    if is_plausible(created, received_at):
        return created, RecordedAtSource.METADATA
    if is_plausible(sent_at, received_at):
        return sent_at, RecordedAtSource.WHATSAPP
    return received_at, RecordedAtSource.RECEIVED


def sent_at_from_timestamp(seconds: int | None) -> datetime | None:
    """WhatsApp's messageTimestamp (seconds since 1970) as a UTC datetime."""
    if seconds is None or seconds <= 0:
        return None
    try:
        return datetime.fromtimestamp(seconds, UTC)
    except (OverflowError, OSError, ValueError):
        return None


def recorded_at_columns(
    *, received_at: datetime, sent_at: datetime | None, probe: ProbeReport | None = None
) -> dict[str, str]:
    """The jobs columns recorded_at and recorded_at_source, ready to store."""
    moment, source = choose_recorded_at(received_at=received_at, sent_at=sent_at, probe=probe)
    return {"recorded_at": to_db_time(moment), "recorded_at_source": str(source)}
