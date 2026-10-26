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
    build_probe_command,
    build_proxy_command,
    build_test_clip_command,
    parse_probe_output,
)

ffmpeg_missing = shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None


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
