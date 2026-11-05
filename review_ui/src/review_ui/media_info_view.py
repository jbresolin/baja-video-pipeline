"""The "file details" panel under the player: the raw's ffprobe report, readable.

shared.media_details does the reading (which tag means what); this module
only turns the result into labelled rows for the template. A row whose
value is unknown is left out, so the panel never shows a column of dashes.
Labels come from the pt-BR catalog; numbers use a decimal comma.
"""

from dataclasses import dataclass
from datetime import datetime

from review_ui.catalog import text
from review_ui.formatting import (
    clock_text,
    decimal,
    format_bytes,
    format_duration,
    short_number,
)
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
    rate = f"{details.frame_rate:.3f}".rstrip("0").rstrip(".").replace(".", ",") + " fps"
    if details.variable_frame_rate:
        rate += " " + text("media.variable")
    return rate


def _resolution_text(details: MediaDetails) -> str | None:
    if not (details.width and details.height):
        return None
    resolution = f"{details.width}×{details.height}"
    if details.display_aspect and details.display_aspect not in ("0:1", "N/A"):
        resolution += f" ({details.display_aspect})"
    return resolution


def _video_codec_text(details: MediaDetails) -> str | None:
    if not details.video_codec:
        return None
    if details.video_profile:
        return f"{details.video_codec} ({details.video_profile})"
    return details.video_codec


def _colour_text(details: MediaDetails) -> str | None:
    parts = []
    if details.color_transfer:
        parts.append(text("media.transfer", value=details.color_transfer))
    if details.color_primaries:
        parts.append(text("media.primaries", value=details.color_primaries))
    return ", ".join(parts) or None


def _audio_text(details: MediaDetails) -> str | None:
    if not details.audio_codec:
        return None
    parts = [details.audio_codec]
    if details.audio_channel_layout:
        parts.append(details.audio_channel_layout)
    elif details.audio_channels:
        parts.append(text("media.channels", count=details.audio_channels))
    if details.audio_sample_rate:
        parts.append(f"{short_number(details.audio_sample_rate / 1000)} kHz")
    return ", ".join(parts)


def _bit_rate_text(bit_rate: int | None) -> str | None:
    return None if bit_rate is None else f"{decimal(bit_rate / 1_000_000, 1)} Mbps"


def _recorded_at_text(creation_time: str | None) -> str | None:
    """The file's own recording time in São Paulo time; as written when it is not ISO 8601."""
    if not creation_time:
        return None
    try:
        moment = datetime.fromisoformat(creation_time)
    except ValueError:
        return creation_time
    if moment.tzinfo is None:
        # No offset in the tag: the phone's local time, shown as it is.
        return moment.strftime("%d/%m/%Y %H:%M:%S")
    return clock_text(moment)


def media_panel(job: Job) -> MediaPanel | None:
    """The panel for a job, or None while no holder has sent the report yet."""
    if job.media_info is None:
        return None
    details = summarize_probe_report(job.media_info)
    candidate_rows = [
        (text("media.recorded_with"), details.device or details.encoder),
        (text("media.recorded_at"), _recorded_at_text(details.creation_time)),
        (text("media.container"), details.container),
        (
            text("media.duration"),
            format_duration(details.duration_s) if details.duration_s else None,
        ),
        (text("media.file_size"), format_bytes(details.size_bytes) if details.size_bytes else None),
        (text("media.bit_rate"), _bit_rate_text(details.bit_rate)),
        (text("media.video"), _video_codec_text(details)),
        (text("media.resolution"), _resolution_text(details)),
        (text("media.rotation"), f"{details.rotation}°" if details.rotation else None),
        (text("media.frame_rate"), _frame_rate_text(details)),
        (text("media.pixel_format"), details.pixel_format),
        (text("media.colour"), _colour_text(details)),
        (text("media.audio"), _audio_text(details) or text("media.no_audio")),
    ]
    return MediaPanel(
        dynamic_range=details.dynamic_range,
        location_present=details.location_present,
        rows=[(label, value) for label, value in candidate_rows if value],
        other_tags=sorted(details.other_tags.items()),
    )
