"""Optional test that runs the real ffmpeg on a tiny generated clip.

Skipped automatically when ffmpeg/ffprobe are not installed (for example
in CI), because the rest of the suite must never need external tools.
Run only this test with:  uv run pytest -m integration
"""

import shutil
import subprocess

import pytest

from shared.ffmpeg import (
    X264_ENCODER,
    build_final_render_command,
    build_proxy_command,
    build_test_clip_command,
)
from shared.media_probe import build_probe_command, parse_probe_output

ffmpeg_missing = shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None


def ffmpeg_has_filters(*names: str) -> bool:
    """True if this ffmpeg build includes every named filter (zscale needs libzimg)."""
    if ffmpeg_missing:
        return False
    listing = subprocess.run(["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True)
    available = {line.split()[1] for line in listing.stdout.splitlines() if len(line.split()) > 1}
    return all(name in available for name in names)


def probe(path):
    output = subprocess.run(build_probe_command(path), capture_output=True, text=True, check=True)
    return parse_probe_output(output.stdout)


@pytest.mark.integration
@pytest.mark.skipif(ffmpeg_missing, reason="ffmpeg/ffprobe not installed")
def test_real_ffmpeg_makes_a_proxy_and_a_final_clip(tmp_path):
    # Step 1: make a 2-second 720p test clip with sound (ffmpeg generates
    # the picture and a tone itself, no input file needed).
    raw = tmp_path / "raw.mp4"
    subprocess.run(build_test_clip_command(raw, duration_s=2.0), check=True, capture_output=True)

    # Step 2: build the proxy with libx264 (works on any machine, no GPU).
    proxy = tmp_path / "proxy.mp4"
    subprocess.run(build_proxy_command(raw, proxy, X264_ENCODER), check=True, capture_output=True)

    # The proxy must really be 480 lines high, keep its sound, and last
    # about as long as the raw (a little rounding is normal).
    proxy_info = probe(proxy)
    assert proxy_info.height == 480
    assert proxy_info.has_audio
    assert proxy_info.duration_s == pytest.approx(2.0, abs=0.15)

    # Step 3: final render of the cut 0.5 s → 1.5 s, with loudnorm.
    clip = tmp_path / "clip.mp4"
    command = build_final_render_command(raw, clip, 0.5, 1.5, X264_ENCODER)
    subprocess.run(command, check=True, capture_output=True)

    # The clip is 1 second long and 1080 lines high (scaled up from 720).
    clip_info = probe(clip)
    assert clip_info.duration_s == pytest.approx(1.0, abs=0.15)
    assert clip_info.height == 1080
    assert clip_info.has_audio


@pytest.mark.integration
@pytest.mark.skipif(
    not ffmpeg_has_filters("zscale", "tonemap"), reason="ffmpeg without zscale/tonemap"
)
def test_real_ffmpeg_detects_hdr_and_tone_maps_it(tmp_path):
    # Step 1: a test clip labelled as HDR10 (PQ transfer, BT.2020 colours).
    raw = tmp_path / "hdr.mp4"
    command = build_test_clip_command(raw, duration_s=1.0, tag_as_hdr=True)
    subprocess.run(command, check=True, capture_output=True)

    # Step 2: ffprobe reports it as HDR, so the worker would tone-map.
    raw_info = probe(raw)
    assert raw_info.color_transfer == "smpte2084"
    assert raw_info.is_hdr

    # Step 3: real ffmpeg accepts our tone-mapping filters (a typo in the
    # filter text would make it exit with an error and check=True raise),
    # and both outputs come out as ordinary SDR (BT.709) video.
    proxy = tmp_path / "proxy.mp4"
    proxy_command = build_proxy_command(raw, proxy, X264_ENCODER, tone_map_hdr=True)
    subprocess.run(proxy_command, check=True, capture_output=True)
    clip = tmp_path / "clip.mp4"
    clip_command = build_final_render_command(raw, clip, 0.0, 0.5, X264_ENCODER, tone_map_hdr=True)
    subprocess.run(clip_command, check=True, capture_output=True)

    for output in (proxy, clip):
        info = probe(output)
        assert info.color_transfer == "bt709"
        assert not info.is_hdr
