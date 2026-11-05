"""The "file details" panel under the player: the raw's ffprobe report, readable.

shared.media_details does the reading (which tag means what); this module
only turns the result into labelled rows for the template. A row whose
value is unknown is left out, so the panel never shows a column of dashes.
"""

from dataclasses import dataclass

from review_ui.formatting import format_bytes, format_duration
from shared.media_details import MediaDetails, summarize_probe_report
from shared.models import Job


@dataclass(frozen=True)
class MediaPanel:
    # "HDR" / "SDR" for the badge, or None when there is no video stream.
    dynamic_range: str | None
    location_present: bool
    # (label, value) in display order; only known values.
    rows: list[tuple[str, str]]
    # (tag name, value) for the collapsed "all tags" list.
    other_tags: list[tuple[str, str]]


def _frame_rate_text(details: MediaDetails) -> str | None:
    if details.frame_rate is None:
        return None
    text = f"{details.frame_rate:.3f}".rstrip("0").rstrip(".") + " fps"
    if details.variable_frame_rate:
        text += " (variable)"
    return text


def _resolution_text(details: MediaDetails) -> str | None:
    if not (details.width and details.height):
        return None
    text = f"{details.width}×{details.height}"
    if details.display_aspect and details.display_aspect not in ("0:1", "N/A"):
        text += f" ({details.display_aspect})"
    return text


def _video_codec_text(details: MediaDetails) -> str | None:
    if not details.video_codec:
        return None
    if details.video_profile:
        return f"{details.video_codec} ({details.video_profile})"
    return details.video_codec


def _colour_text(details: MediaDetails) -> str | None:
    parts = []
    if details.color_transfer:
        parts.append(f"transfer {details.color_transfer}")
    if details.color_primaries:
        parts.append(f"primaries {details.color_primaries}")
    return ", ".join(parts) or None


def _audio_text(details: MediaDetails) -> str | None:
    if not details.audio_codec:
        return None
    parts = [details.audio_codec]
    if details.audio_channel_layout:
        parts.append(details.audio_channel_layout)
    elif details.audio_channels:
        parts.append(f"{details.audio_channels} ch")
    if details.audio_sample_rate:
        parts.append(f"{details.audio_sample_rate / 1000:g} kHz")
    return ", ".join(parts)


def _bit_rate_text(bit_rate: int | None) -> str | None:
    return None if bit_rate is None else f"{bit_rate / 1_000_000:.1f} Mbps"


def media_panel(job: Job) -> MediaPanel | None:
    """The panel for a job, or None while no holder has sent the report yet."""
    if job.media_info is None:
        return None
    details = summarize_probe_report(job.media_info)
    candidate_rows = [
        ("Recorded with", details.device or details.encoder),
        ("Recorded at", details.creation_time),
        ("Container", details.container),
        ("Duration", format_duration(details.duration_s) if details.duration_s else None),
        ("File size", format_bytes(details.size_bytes) if details.size_bytes else None),
        ("Bitrate", _bit_rate_text(details.bit_rate)),
        ("Video", _video_codec_text(details)),
        ("Resolution", _resolution_text(details)),
        ("Rotation", f"{details.rotation}°" if details.rotation else None),
        ("Frame rate", _frame_rate_text(details)),
        ("Pixel format", details.pixel_format),
        ("Colour", _colour_text(details)),
        ("Audio", _audio_text(details) or "none"),
    ]
    return MediaPanel(
        dynamic_range=details.dynamic_range,
        location_present=details.location_present,
        rows=[(label, value) for label, value in candidate_rows if value],
        other_tags=sorted(details.other_tags.items()),
    )
