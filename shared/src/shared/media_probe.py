"""Ask ffprobe what a media file contains, and decide whether it is HDR.

args = build_probe_command(path)
info = parse_probe_output(subprocess.run(args, capture_output=True,
                                         text=True, check=True).stdout)
if info.is_hdr: ...  # pass tone_map_hdr=True to the ffmpeg builders
"""

import json
from dataclasses import dataclass
from pathlib import Path

# ffprobe's `color_transfer` values that mean HDR (design §15):
#   smpte2084    = PQ, used by HDR10 / Dolby Vision (e.g. iPhone "HDR" video)
#   arib-std-b67 = HLG, used by many Android phones and broadcast cameras
HDR_COLOR_TRANSFERS = frozenset({"smpte2084", "arib-std-b67"})


def build_probe_command(media_path: Path, ffprobe_binary: str = "ffprobe") -> list[str]:
    """ffprobe command printing format and stream info as JSON."""
    return [
        ffprobe_binary, "-v", "error",
        "-print_format", "json",
        "-show_format", "-show_streams",
        str(media_path),
    ]  # fmt: skip


@dataclass(frozen=True)
class MediaInfo:
    duration_s: float | None
    has_audio: bool
    width: int | None
    height: int | None
    # Transfer characteristic of the first video stream, e.g. "bt709",
    # "smpte2084"; None when ffprobe does not report one.
    color_transfer: str | None = None
    # Frames per second of the first video stream (e.g. 29.97), from
    # avg_frame_rate, else r_frame_rate; None when neither is usable.
    frame_rate: float | None = None

    @property
    def is_hdr(self) -> bool:
        """True when the video must be tone-mapped before it can be shown as SDR."""
        return self.color_transfer in HDR_COLOR_TRANSFERS


def parse_frame_rate(rate_text: str | None) -> float | None:
    """ffprobe writes rates as fractions ("30000/1001", "30/1"); "0/0" means unknown."""
    if not rate_text:
        return None
    numerator, _, denominator = rate_text.partition("/")
    try:
        value = float(numerator) / float(denominator or "1")
    except (ValueError, ZeroDivisionError):
        return None
    return value if value > 0 else None


def parse_probe_output(probe_json: str) -> MediaInfo:
    """Read the facts we need from build_probe_command's output."""
    probe = json.loads(probe_json)
    streams = probe.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), {})
    duration_text = probe.get("format", {}).get("duration")
    return MediaInfo(
        duration_s=float(duration_text) if duration_text is not None else None,
        has_audio=any(s.get("codec_type") == "audio" for s in streams),
        width=video.get("width"),
        height=video.get("height"),
        color_transfer=video.get("color_transfer"),
        frame_rate=(
            parse_frame_rate(video.get("avg_frame_rate"))
            or parse_frame_rate(video.get("r_frame_rate"))
        ),
    )
