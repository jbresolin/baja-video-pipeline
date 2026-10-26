"""Tests for shared.media_probe: reading ffprobe output, deciding HDR or not.

These never run ffprobe; they feed it small, hand-written examples of the
JSON ffprobe prints.
"""

import json

import pytest

from shared.ffmpeg import build_proxy_command
from shared.media_probe import build_probe_command, parse_probe_output


def probe_json(color_transfer: str | None = None, with_audio: bool = True) -> str:
    """Build a trimmed ffprobe answer with the given video colour transfer."""
    video = {"codec_type": "video", "width": 1920, "height": 1080}
    if color_transfer is not None:
        video["color_transfer"] = color_transfer
    streams = [video, {"codec_type": "audio"}] if with_audio else [video]
    return json.dumps({"streams": streams, "format": {"duration": "12.345000"}})


def test_probe_command_asks_for_json():
    args = build_probe_command("raw.mov")
    assert args[0] == "ffprobe"
    assert args[args.index("-print_format") + 1] == "json"


def test_parse_probe_output_reads_duration_audio_and_size():
    info = parse_probe_output(probe_json("bt709"))
    assert info.duration_s == pytest.approx(12.345)
    assert info.has_audio is True
    assert (info.width, info.height) == (1920, 1080)


def test_parse_probe_output_without_audio_or_duration():
    info = parse_probe_output('{"streams": [{"codec_type": "video"}], "format": {}}')
    assert info.has_audio is False
    assert info.duration_s is None


@pytest.mark.parametrize(
    ("color_transfer", "expected_hdr"),
    [
        ("smpte2084", True),  # PQ: HDR10 / Dolby Vision, e.g. iPhone HDR video
        ("arib-std-b67", True),  # HLG: many Android phones
        ("bt709", False),  # ordinary SDR video
        ("smpte170m", False),  # old SD video, still SDR
        (None, False),  # not reported at all: treat as SDR
    ],
)
def test_hdr_is_detected_from_color_transfer(color_transfer, expected_hdr):
    info = parse_probe_output(probe_json(color_transfer))
    assert info.color_transfer == color_transfer
    assert info.is_hdr is expected_hdr


def test_probe_result_drives_the_proxy_filter_chain():
    # How the worker will use it: probe, then pass is_hdr to the builder.
    # An HDR probe result gives a tone-mapped proxy, an SDR one does not.
    hdr = parse_probe_output(probe_json("smpte2084"))
    sdr = parse_probe_output(probe_json("bt709"))
    hdr_args = build_proxy_command("raw", "out", "libx264", tone_map_hdr=hdr.is_hdr)
    sdr_args = build_proxy_command("raw", "out", "libx264", tone_map_hdr=sdr.is_hdr)
    assert "tonemap=tonemap=hable" in hdr_args[hdr_args.index("-vf") + 1]
    assert "tonemap" not in sdr_args[sdr_args.index("-vf") + 1]
