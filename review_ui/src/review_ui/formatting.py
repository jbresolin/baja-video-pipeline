"""Small text formatters for the templates (registered as Jinja filters).

Brazilian conventions everywhere (LIVESTACK-39): dates as 05/11/2026 09:23:41
on a 24-hour clock in São Paulo time, and a decimal comma ("1,5 GB").
static/js/format.js does the same for the page scripts; both are tested with
the same inputs and the same expected strings (tests/review_ui/test_formats.py).
"""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from review_ui.catalog import has_text, text

# Every time on screen is in the event's own time zone, whatever the
# reviewer's laptop is set to.
DISPLAY_TIME_ZONE = ZoneInfo("America/Sao_Paulo")

_BYTE_UNITS = ["B", "KB", "MB", "GB", "TB"]


def decimal(value: float, places: int) -> str:
    """A number with a decimal comma: decimal(1.5, 1) → "1,5"."""
    return f"{value:.{places}f}".replace(".", ",")


def short_number(value: float) -> str:
    """Like "%g" but with a decimal comma: 29.97 → "29,97", 30.0 → "30"."""
    return f"{value:g}".replace(".", ",")


def format_bytes(size: int | None) -> str:
    """1_500_000_000 → "1,5 GB". Decimal units, like the BVP_*_GB settings."""
    if size is None:
        return "—"
    value = float(size)
    for unit in _BYTE_UNITS:
        if value < 1000 or unit == _BYTE_UNITS[-1]:
            return f"{value:.0f} {unit}" if unit == "B" else f"{decimal(value, 1)} {unit}"
        value /= 1000
    raise AssertionError("unreachable")


def format_timecode(seconds: float | None) -> str:
    """Position in a video with milliseconds: 62.5 → "1:02,500"."""
    if seconds is None:
        return "—"
    minutes, rest = divmod(seconds, 60)
    return f"{int(minutes)}:{decimal(rest, 3).zfill(6)}"


def format_duration(seconds: float | None) -> str:
    """Length of a video, whole seconds: 62.5 → "1:02"."""
    if seconds is None:
        return "—"
    minutes, rest = divmod(round(seconds), 60)
    return f"{minutes}:{rest:02d}"


def short_id(identifier: str) -> str:
    """First 8 characters of an id; enough to tell jobs apart on screen."""
    return identifier[:8]


def iso_utc(moment: datetime | None) -> str:
    """Machine-readable time for <time datetime="…">; the page script keeps it current."""
    if moment is None:
        return ""
    return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")


def clock_text(moment: datetime | None) -> str:
    """Date and time in São Paulo: "05/11/2026 09:23:41"."""
    if moment is None:
        return "—"
    return moment.astimezone(DISPLAY_TIME_ZONE).strftime("%d/%m/%Y %H:%M:%S")


def short_clock_text(moment: datetime) -> str:
    """Hours and minutes in São Paulo: "09:23"."""
    return moment.astimezone(DISPLAY_TIME_ZONE).strftime("%H:%M")


def recorded_time_text(moment: datetime | None, reference: datetime) -> str:
    """When a video was filmed, short (LIVESTACK-45): "14:02" on the same São
    Paulo day as `reference` (today in the queue, this video's day on the job
    page), else with the date, "26/03/2026 14:02"."""
    if moment is None:
        return "—"
    local = moment.astimezone(DISPLAY_TIME_ZONE)
    if local.date() == reference.astimezone(DISPLAY_TIME_ZONE).date():
        return local.strftime("%H:%M")
    return local.strftime("%d/%m/%Y %H:%M")


def age_text(moment: datetime | None, now: datetime) -> str:
    """How long ago, roughly: "há 12 s", "há 5 min", "há 3 h", "há 2 d"; "nunca" for None."""
    if moment is None:
        return text("script.time.never")
    seconds = max(0, int((now - moment).total_seconds()))
    if seconds < 60:
        return text("script.time.seconds_ago", n=seconds)
    if seconds < 3600:
        return text("script.time.minutes_ago", n=seconds // 60)
    if seconds < 86400:
        return text("script.time.hours_ago", n=seconds // 3600)
    return text("script.time.days_ago", n=seconds // 86400)


def relative_time_text(moment: datetime | None, now: datetime) -> str:
    """ "há 5 min (09:18)": what a relative <time> shows (js/format.js relativeTimeText)."""
    if moment is None:
        return "—"
    return text(
        "script.time.relative_with_clock",
        relative=age_text(moment, now),
        clock=short_clock_text(moment),
    )


def format_rate(value: float | None, unit: str) -> str:
    """A rolling stat, or a dash while the worker has not been measured yet."""
    if value is None:
        return "—"
    return f"{decimal(value, 1)} {unit}"


def percent(part: int | float, whole: int | float) -> int:
    """part/whole as a whole percentage, clamped to 0–100 (for bar widths)."""
    if whole <= 0:
        return 0
    return max(0, min(100, round(100 * part / whole)))


def state_label(state: str) -> str:
    """A job state for people: awaiting_review → "aguardando revisão".

    The API value stays English; only the label is translated. A state this
    UI does not know yet shows as its name with spaces.
    """
    value = str(state)
    key = f"state.{value}"
    return text(key) if has_text(key) else value.replace("_", " ")
