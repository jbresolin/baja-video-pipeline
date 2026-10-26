"""Build ffmpeg command lines, and pick the H.264 encoder to use.

The builders only return argument lists (["ffmpeg", "-i", ...]); running
them is the caller's job (subprocess.run(args, check=True)). That keeps
them easy to test and easy to print when something goes wrong.
Probing a file (duration, audio, HDR) lives in shared.media_probe.
"""

import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

NVENC_ENCODER = "h264_nvenc"
X264_ENCODER = "libx264"

# Arguments every ffmpeg call starts with: no banner noise, never wait for
# keyboard input (the worker runs as a service), overwrite outputs.
COMMON_FFMPEG_ARGS = ["-hide_banner", "-nostdin", "-y"]

# HDR → SDR (BT.709) tone mapping, inserted before the yuv420p conversion
# when shared.media_probe says the raw is HDR (design §15). Step by step:
#   zscale=transfer=linear:npl=100  undo the PQ/HLG curve into linear light
#                                   (npl = nominal peak luminance, nits)
#   format=gbrpf32le                float RGB, which tonemap needs
#   zscale=primaries=bt709          BT.2020 colours → BT.709 colours
#   tonemap=tonemap=hable:desat=0   squeeze highlights into SDR range
#   zscale=transfer=bt709:...       back to the normal SDR video curve
TONE_MAP_HDR_TO_SDR = (
    "zscale=transfer=linear:npl=100,"
    "format=gbrpf32le,"
    "zscale=primaries=bt709,"
    "tonemap=tonemap=hable:desat=0,"
    "zscale=transfer=bt709:matrix=bt709:range=tv"
)


def _with_optional_tone_map(filters: list[str], tone_map_hdr: bool) -> list[str]:
    """Append the tone-mapping stage to a filter list when the raw is HDR."""
    return [*filters, TONE_MAP_HDR_TO_SDR] if tone_map_hdr else filters


class FfmpegNotFoundError(RuntimeError):
    pass


@dataclass(frozen=True)
class ProxySettings:
    """480p ~1.5 Mbps review proxy (design §4)."""

    height: int = 480
    video_bitrate_kbps: int = 1500
    # Constant frame rate, so a time in the proxy is the same time in the raw.
    frame_rate: int = 30
    # A keyframe every 2 s keeps seeking in the browser player snappy.
    keyframe_interval_frames: int = 60
    audio_bitrate_kbps: int = 96


@dataclass(frozen=True)
class FinalRenderSettings:
    height: int = 1080
    # EBU-style loudness targets for loudnorm: integrated, true peak, range.
    loudness_integrated_lufs: float = -16.0
    loudness_true_peak_db: float = -1.5
    loudness_range_lu: float = 11.0
    audio_bitrate_kbps: int = 192
    # Quality targets (lower = better/bigger): NVENC "cq" and x264 "crf".
    nvenc_constant_quality: int = 21
    x264_crf: int = 20


DEFAULT_PROXY_SETTINGS = ProxySettings()
DEFAULT_FINAL_RENDER_SETTINGS = FinalRenderSettings()


def _video_encoder_args_for_bitrate(encoder: str, bitrate_kbps: int) -> list[str]:
    """Encoder flags for a target bitrate (used by the proxy)."""
    rate = [
        "-b:v", f"{bitrate_kbps}k",
        "-maxrate", f"{bitrate_kbps}k",
        "-bufsize", f"{bitrate_kbps * 2}k",
    ]  # fmt: skip
    if encoder == NVENC_ENCODER:
        return ["-c:v", NVENC_ENCODER, "-preset", "p4", *rate]
    return ["-c:v", X264_ENCODER, "-preset", "fast", *rate]


def _video_encoder_args_for_quality(encoder: str, settings: FinalRenderSettings) -> list[str]:
    """Encoder flags for a target quality (used by the final render)."""
    if encoder == NVENC_ENCODER:
        quality = str(settings.nvenc_constant_quality)
        return ["-c:v", NVENC_ENCODER, "-preset", "p5", "-rc", "vbr", "-cq", quality, "-b:v", "0"]
    return ["-c:v", X264_ENCODER, "-preset", "fast", "-crf", str(settings.x264_crf)]


def build_proxy_command(
    raw_path: Path,
    proxy_path: Path,
    encoder: str,
    settings: ProxySettings = DEFAULT_PROXY_SETTINGS,
    ffmpeg_binary: str = "ffmpeg",
    tone_map_hdr: bool = False,
) -> list[str]:
    """Command that renders the review proxy from a raw.

    Pass tone_map_hdr=MediaInfo.is_hdr so HDR phone footage is not washed out.
    """
    # scale=-2:480 keeps the aspect ratio and an even width (H.264 needs it);
    # fps=30 makes the frame rate constant; yuv420p plays in every browser.
    # Tone mapping runs after the scale, so it works on the small frame.
    steps = [f"scale=-2:{settings.height}", f"fps={settings.frame_rate}"]
    video_filter = ",".join([*_with_optional_tone_map(steps, tone_map_hdr), "format=yuv420p"])
    return [
        ffmpeg_binary, *COMMON_FFMPEG_ARGS,
        "-i", str(raw_path),
        "-vf", video_filter,
        "-fps_mode", "cfr",
        *_video_encoder_args_for_bitrate(encoder, settings.video_bitrate_kbps),
        "-g", str(settings.keyframe_interval_frames),
        "-c:a", "aac", "-b:a", f"{settings.audio_bitrate_kbps}k", "-ac", "2",
        # Move the index to the front so the browser can start playing at once.
        "-movflags", "+faststart",
        str(proxy_path),
    ]  # fmt: skip


def build_thumbnail_command(
    source_path: Path,
    thumbnail_path: Path,
    at_seconds: float = 1.0,
    width: int = 320,
    ffmpeg_binary: str = "ffmpeg",
) -> list[str]:
    """Command that grabs one JPEG frame for the review queue."""
    return [
        ffmpeg_binary, *COMMON_FFMPEG_ARGS,
        "-ss", f"{at_seconds:.3f}",
        "-i", str(source_path),
        "-frames:v", "1",
        "-vf", f"scale={width}:-2",
        "-q:v", "4",
        str(thumbnail_path),
    ]  # fmt: skip


def build_final_render_command(
    raw_path: Path,
    clip_path: Path,
    cut_in_s: float,
    cut_out_s: float,
    encoder: str,
    overlay_image: Path | None = None,
    has_audio: bool = True,
    settings: FinalRenderSettings = DEFAULT_FINAL_RENDER_SETTINGS,
    ffmpeg_binary: str = "ffmpeg",
    tone_map_hdr: bool = False,
) -> list[str]:
    """Command that trims the raw, draws the overlay and normalises loudness.

    `overlay_image` is a transparent PNG (team, car, pass) drawn in the
    bottom-left corner; the worker renders it. Without it, no overlay.
    `has_audio=False` (from ffprobe) skips the audio chain entirely.
    `tone_map_hdr=True` (MediaInfo.is_hdr) converts HDR footage to SDR
    first, so the SDR overlay is drawn onto an SDR picture.
    """
    if cut_out_s <= cut_in_s:
        raise ValueError("cut_out_s must be greater than cut_in_s")
    duration_s = cut_out_s - cut_in_s

    inputs = ["-ss", f"{cut_in_s:.3f}", "-i", str(raw_path)]
    video_steps = _with_optional_tone_map([f"scale=-2:{settings.height}"], tone_map_hdr)
    scale = "[0:v]" + ",".join(video_steps)
    if overlay_image is None:
        filters = [f"{scale},format=yuv420p[video]"]
    else:
        inputs += ["-i", str(overlay_image)]
        filters = [
            f"{scale}[base]",
            "[base][1:v]overlay=x=0:y=main_h-overlay_h,format=yuv420p[video]",
        ]
    maps = ["-map", "[video]"]
    audio_args = ["-an"]
    if has_audio:
        filters.append(
            "[0:a:0]loudnorm="
            f"I={settings.loudness_integrated_lufs}:"
            f"TP={settings.loudness_true_peak_db}:"
            f"LRA={settings.loudness_range_lu}[audio]"
        )
        maps += ["-map", "[audio]"]
        # loudnorm outputs 192 kHz internally; bring it back to 48 kHz.
        audio_args = ["-c:a", "aac", "-b:a", f"{settings.audio_bitrate_kbps}k", "-ar", "48000"]

    return [
        ffmpeg_binary, *COMMON_FFMPEG_ARGS,
        *inputs,
        "-filter_complex", ";".join(filters),
        *maps,
        "-t", f"{duration_s:.3f}",
        *_video_encoder_args_for_quality(encoder, settings),
        *audio_args,
        "-movflags", "+faststart",
        str(clip_path),
    ]  # fmt: skip


def build_test_clip_command(
    output_path: Path,
    duration_s: float = 2.0,
    width: int = 1280,
    height: int = 720,
    frame_rate: int = 30,
    ffmpeg_binary: str = "ffmpeg",
    tag_as_hdr: bool = False,
) -> list[str]:
    """Command that makes a small synthetic MP4 (test pattern + 440 Hz tone).

    Uses ffmpeg's built-in generators, so no input file is needed.
    `tag_as_hdr=True` labels the clip as HDR10 (PQ, BT.2020). The pixels
    are not real HDR, but ffprobe reports smpte2084, which is enough to
    exercise detection and the tone-mapping filters.
    """
    hdr_tags = (
        ["-color_primaries", "bt2020", "-color_trc", "smpte2084", "-colorspace", "bt2020nc"]
        if tag_as_hdr
        else []
    )
    return [
        ffmpeg_binary, *COMMON_FFMPEG_ARGS,
        "-f", "lavfi", "-i", f"testsrc2=size={width}x{height}:rate={frame_rate}",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
        "-t", f"{duration_s:.3f}",
        "-c:v", X264_ENCODER, "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        *hdr_tags,
        "-c:a", "aac",
        "-shortest",
        str(output_path),
    ]  # fmt: skip


# A function that runs a command and returns the finished process. Tests
# replace it with a fake so no real ffmpeg is needed.
CommandRunner = Callable[[list[str]], subprocess.CompletedProcess]


def _run_command(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, timeout=60, check=False)


def encoder_is_listed(encoders_output: str, encoder_name: str) -> bool:
    """True if `ffmpeg -encoders` output has a line for `encoder_name`.

    Lines look like: " V....D h264_nvenc   NVIDIA NVENC H.264 encoder".
    """
    for line in encoders_output.splitlines():
        words = line.split()
        if len(words) >= 2 and words[1] == encoder_name:
            return True
    return False


def detect_encoder(ffmpeg_binary: str = "ffmpeg", run: CommandRunner = _run_command) -> str:
    """Return "h264_nvenc" if it is listed and actually works, else "libx264".

    Being listed is not enough: the BtbN builds list NVENC even on PCs
    without an NVIDIA GPU. So we also encode one black frame with it.
    """
    try:
        listing = run([ffmpeg_binary, "-hide_banner", "-encoders"])
    except FileNotFoundError as missing:
        raise FfmpegNotFoundError(f"cannot run '{ffmpeg_binary}'") from missing

    if listing.returncode != 0 or not encoder_is_listed(listing.stdout, NVENC_ENCODER):
        return X264_ENCODER

    trial_encode = run([
        ffmpeg_binary, "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "color=black:size=256x256:duration=0.1",
        "-frames:v", "1",
        "-c:v", NVENC_ENCODER,
        "-f", "null", "-",
    ])  # fmt: skip
    return NVENC_ENCODER if trial_encode.returncode == 0 else X264_ENCODER
