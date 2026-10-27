"""Small text formatters for the templates (registered as Jinja filters)."""

from datetime import UTC, datetime

_BYTE_UNITS = ["B", "KB", "MB", "GB", "TB"]


def format_bytes(size: int | None) -> str:
    """1_500_000_000 → "1.5 GB". Decimal units, like the BVP_*_GB settings."""
    if size is None:
        return "—"
    value = float(size)
    for unit in _BYTE_UNITS:
        if value < 1000 or unit == _BYTE_UNITS[-1]:
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1000
    raise AssertionError("unreachable")


def format_timecode(seconds: float | None) -> str:
    """Position in a video with milliseconds: 62.5 → "1:02.500"."""
    if seconds is None:
        return "—"
    minutes, rest = divmod(seconds, 60)
    return f"{int(minutes)}:{rest:06.3f}"


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
    """Machine-readable time for <time datetime="…">; the browser localizes it."""
    if moment is None:
        return ""
    return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")


def clock_utc(moment: datetime | None) -> str:
    """Readable fallback shown until JavaScript swaps in the local time."""
    if moment is None:
        return "—"
    return moment.astimezone(UTC).strftime("%d/%m %H:%M:%S UTC")


def age_text(moment: datetime | None, now: datetime) -> str:
    """How long ago, roughly: "12 s ago", "5 min ago", "3 h ago", "2 d ago"."""
    if moment is None:
        return "never"
    seconds = max(0, int((now - moment).total_seconds()))
    if seconds < 60:
        return f"{seconds} s ago"
    if seconds < 3600:
        return f"{seconds // 60} min ago"
    if seconds < 86400:
        return f"{seconds // 3600} h ago"
    return f"{seconds // 86400} d ago"


def format_rate(value: float | None, unit: str) -> str:
    """A rolling stat, or a dash while the worker has not been measured yet."""
    if value is None:
        return "—"
    return f"{value:.1f} {unit}"


def percent(part: int | float, whole: int | float) -> int:
    """part/whole as a whole percentage, clamped to 0–100 (for bar widths)."""
    if whole <= 0:
        return 0
    return max(0, min(100, round(100 * part / whole)))


def state_label(state: str) -> str:
    """State name for people: awaiting_review → awaiting review."""
    return str(state).replace("_", " ")
