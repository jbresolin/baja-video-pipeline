"""The raw's ffprobe report: strip location tags, and summarise it for people.

    report = probe_report_from_ffprobe(json.loads(ffprobe_stdout))  # worker
    details = summarize_probe_report(report)                        # review UI

The first holder sends the report with the proxy (PUT /jobs/{id}/media-info)
and the review UI shows it under the player (docs/api.md §7.1). Two rules:

* **Location never leaves the worker.** Phones write GPS coordinates into
  tags such as `location` or `com.apple.quicktime.location.ISO6709`. Every
  tag whose name mentions location or GPS is removed; only the fact that
  there was one (`location_present`) is kept. The orchestrator runs the
  same function again on what it receives, so a worker cannot store one.
* **Summaries never fail.** ffprobe output varies between files and
  versions; every value below is optional and missing ones are left out.
"""

import re
from dataclasses import dataclass, field
from typing import Any

from shared.media_probe import HDR_COLOR_TRANSFERS, parse_frame_rate
from shared.models import ProbeReport

# Tag names (lowercased) containing one of these are location data.
_LOCATION_TAG_WORDS = ("location", "gps", "latitude", "longitude", "xyz")

# Where phones and cameras say who made them. Apple writes QuickTime
# metadata keys; Android writes com.android.*; others only an encoder tag.
APPLE_DEVICE_TAGS = (
    "com.apple.quicktime.make",
    "com.apple.quicktime.model",
    "com.apple.quicktime.software",
)
ANDROID_DEVICE_TAGS = (
    "com.android.manufacturer",
    "com.android.model",
    "com.android.version",
)
# Tags already shown in their own row, so "all tags" does not repeat them.
_SHOWN_TAGS = frozenset(
    {*APPLE_DEVICE_TAGS, *ANDROID_DEVICE_TAGS, "encoder", "creation_time",
     "com.apple.quicktime.creationdate", "rotate"}
)  # fmt: skip

# Average and nominal rates further apart than this (frames per second)
# mean the phone recorded with a variable frame rate.
_VARIABLE_RATE_TOLERANCE = 0.05


def is_location_tag(name: str) -> bool:
    lowered = name.lower()
    return any(word in lowered for word in _LOCATION_TAG_WORDS)


def _without_location(tags: Any) -> tuple[dict[str, Any], bool]:
    """(tags minus location tags, whether any were removed)."""
    if not isinstance(tags, dict):
        return {}, False
    kept = {name: value for name, value in tags.items() if not is_location_tag(str(name))}
    return kept, len(kept) != len(tags)


def probe_report_from_ffprobe(probe: dict[str, Any]) -> ProbeReport:
    """ffprobe's parsed JSON → a ProbeReport with every location tag removed."""
    format_section = dict(probe.get("format") or {})
    format_tags, location_present = _without_location(format_section.get("tags"))
    if "tags" in format_section:
        format_section["tags"] = format_tags
    streams = []
    for stream in probe.get("streams") or []:
        stream = dict(stream)
        stream_tags, stream_had_location = _without_location(stream.get("tags"))
        if "tags" in stream:
            stream["tags"] = stream_tags
        location_present = location_present or stream_had_location
        streams.append(stream)
    return ProbeReport(format=format_section, streams=streams, location_present=location_present)


def strip_location_tags(report: ProbeReport) -> ProbeReport:
    """The same cleaning for a report that arrived over the API (never trust it)."""
    cleaned = probe_report_from_ffprobe({"format": report.format, "streams": report.streams})
    return cleaned.model_copy(
        update={"location_present": report.location_present or cleaned.location_present}
    )


@dataclass(frozen=True)
class MediaDetails:
    """What the panel under the player shows. Every field is optional."""

    container: str | None = None
    duration_s: float | None = None
    size_bytes: int | None = None
    bit_rate: int | None = None
    video_codec: str | None = None
    video_profile: str | None = None
    width: int | None = None
    height: int | None = None
    display_aspect: str | None = None
    rotation: int | None = None
    frame_rate: float | None = None
    variable_frame_rate: bool = False
    pixel_format: str | None = None
    color_transfer: str | None = None
    color_primaries: str | None = None
    # "HDR" (PQ/HLG/Dolby Vision) or "SDR"; None without a video stream.
    dynamic_range: str | None = None
    audio_codec: str | None = None
    audio_channels: int | None = None
    audio_channel_layout: str | None = None
    audio_sample_rate: int | None = None
    creation_time: str | None = None
    # e.g. "Apple iPhone 15 Pro (iOS 17.5)", "samsung SM-S918B (Android 14)".
    device: str | None = None
    encoder: str | None = None
    location_present: bool = False
    # Every other tag, "format:<name>" or "<stream type> <n>:<name>".
    other_tags: dict[str, str] = field(default_factory=dict)


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _first_stream(report: ProbeReport, codec_type: str) -> dict[str, Any]:
    return next((s for s in report.streams if s.get("codec_type") == codec_type), {})


def _rotation_of(video: dict[str, Any]) -> int | None:
    """Degrees the player rotates the picture: display-matrix side data, else the old tag."""
    for side_data in video.get("side_data_list") or []:
        if "rotation" in side_data:
            return _int_or_none(side_data["rotation"])
    return _int_or_none((video.get("tags") or {}).get("rotate"))


def _dynamic_range_of(video: dict[str, Any]) -> str | None:
    if not video:
        return None
    side_data_types = {str(s.get("side_data_type", "")) for s in video.get("side_data_list") or []}
    if video.get("color_transfer") in HDR_COLOR_TRANSFERS:
        return "HDR"
    if any("DOVI" in kind or "Dolby Vision" in kind for kind in side_data_types):
        return "HDR"
    return "SDR"


def _frame_rates(video: dict[str, Any]) -> tuple[float | None, bool]:
    """(frames per second, recorded with a variable rate?)."""
    average = parse_frame_rate(video.get("avg_frame_rate"))
    nominal = parse_frame_rate(video.get("r_frame_rate"))
    variable = (
        average is not None
        and nominal is not None
        and abs(average - nominal) > _VARIABLE_RATE_TOLERANCE
    )
    return average or nominal, variable


def _device_of(tags: dict[str, Any]) -> str | None:
    """A readable device line from Apple's or Android's tags, else None."""
    for make_tag, model_tag, software_tag, software_prefix in (
        (*APPLE_DEVICE_TAGS, "iOS "),
        (*ANDROID_DEVICE_TAGS, "Android "),
    ):
        make, model, software = (tags.get(make_tag), tags.get(model_tag), tags.get(software_tag))
        if not (make or model):
            continue
        name = " ".join(str(part) for part in (make, model) if part)
        if software:
            # Apple writes "17.5"; prefix it so a reader knows what it is.
            software_text = str(software)
            if re.fullmatch(r"[\d.]+", software_text):
                software_text = software_prefix + software_text
            name += f" ({software_text})"
        return name
    return None


def _other_tags(report: ProbeReport) -> dict[str, str]:
    collected = {}
    for name, value in (report.format.get("tags") or {}).items():
        if name not in _SHOWN_TAGS:
            collected[f"format:{name}"] = str(value)
    counters: dict[str, int] = {}
    for stream in report.streams:
        kind = str(stream.get("codec_type") or "stream")
        number = counters.get(kind, 0)
        counters[kind] = number + 1
        for name, value in (stream.get("tags") or {}).items():
            if name not in _SHOWN_TAGS:
                collected[f"{kind} {number}:{name}"] = str(value)
    return collected


def summarize_probe_report(report: ProbeReport) -> MediaDetails:
    format_section = report.format
    tags = dict(format_section.get("tags") or {})
    video = _first_stream(report, "video")
    audio = _first_stream(report, "audio")
    # Android writes its device tags on the container; some phones only on
    # the video stream. Container tags win.
    device_tags = {**(video.get("tags") or {}), **tags}
    frame_rate, variable = _frame_rates(video)
    return MediaDetails(
        container=format_section.get("format_long_name") or format_section.get("format_name"),
        duration_s=_float_or_none(format_section.get("duration")),
        size_bytes=_int_or_none(format_section.get("size")),
        bit_rate=_int_or_none(format_section.get("bit_rate")),
        video_codec=video.get("codec_name"),
        video_profile=video.get("profile"),
        width=_int_or_none(video.get("width")),
        height=_int_or_none(video.get("height")),
        display_aspect=video.get("display_aspect_ratio"),
        rotation=_rotation_of(video),
        frame_rate=frame_rate,
        variable_frame_rate=variable,
        pixel_format=video.get("pix_fmt"),
        color_transfer=video.get("color_transfer"),
        color_primaries=video.get("color_primaries"),
        dynamic_range=_dynamic_range_of(video),
        audio_codec=audio.get("codec_name"),
        audio_channels=_int_or_none(audio.get("channels")),
        audio_channel_layout=audio.get("channel_layout"),
        audio_sample_rate=_int_or_none(audio.get("sample_rate")),
        creation_time=tags.get("com.apple.quicktime.creationdate") or tags.get("creation_time"),
        device=_device_of(device_tags),
        encoder=tags.get("encoder") or (video.get("tags") or {}).get("encoder"),
        location_present=report.location_present,
        other_tags=_other_tags(report),
    )
