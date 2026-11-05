"""Tests for shared.media_details: location stripping and the summary under the player.

The inputs are trimmed copies of what ffprobe prints for real phone videos
(`ffprobe -show_format -show_streams -print_format json`).
"""

from shared.media_details import (
    probe_report_from_ffprobe,
    strip_location_tags,
    summarize_probe_report,
)
from shared.models import ProbeReport

IPHONE_HDR = {
    "format": {
        "format_name": "mov,mp4,m4a,3gp,3g2,mj2",
        "format_long_name": "QuickTime / MOV",
        "duration": "31.533333",
        "size": "71234567",
        "bit_rate": "18072000",
        "tags": {
            "creation_time": "2026-11-04T17:31:02.000000Z",
            "com.apple.quicktime.make": "Apple",
            "com.apple.quicktime.model": "iPhone 15 Pro",
            "com.apple.quicktime.software": "17.5",
            "com.apple.quicktime.creationdate": "2026-11-04T14:31:02-0300",
            "com.apple.quicktime.location.ISO6709": "-23.5505-046.6333+760.000/",
            "com.apple.quicktime.location.accuracy.horizontal": "4.7",
        },
    },
    "streams": [
        {
            "codec_type": "video",
            "codec_name": "hevc",
            "profile": "Main 10",
            "width": 1920,
            "height": 1080,
            "display_aspect_ratio": "16:9",
            "pix_fmt": "yuv420p10le",
            "color_transfer": "arib-std-b67",
            "color_primaries": "bt2020",
            "r_frame_rate": "30/1",
            "avg_frame_rate": "30/1",
            "side_data_list": [{"side_data_type": "Display Matrix", "rotation": -90}],
            "tags": {"handler_name": "Core Media Video"},
        },
        {
            "codec_type": "audio",
            "codec_name": "aac",
            "channels": 2,
            "channel_layout": "stereo",
            "sample_rate": "44100",
        },
    ],
}

ANDROID_VFR = {
    "format": {
        "format_name": "mov,mp4,m4a,3gp,3g2,mj2",
        "tags": {
            "com.android.manufacturer": "samsung",
            "com.android.model": "SM-S918B",
            "com.android.version": "14",
            "location": "-23.5505-046.6333/",
            "location-eng": "-23.5505-046.6333/",
        },
    },
    "streams": [
        {
            "codec_type": "video",
            "codec_name": "h264",
            "color_transfer": "bt709",
            "r_frame_rate": "30/1",
            "avg_frame_rate": "1795000/60067",  # 29.88: the phone dropped frames
        }
    ],
}


def test_location_tags_are_removed_and_only_their_presence_is_kept():
    report = probe_report_from_ffprobe(IPHONE_HDR)
    tags = report.format["tags"]
    # Both Apple location keys are gone; everything else is still there.
    assert not any("location" in name for name in tags)
    assert tags["com.apple.quicktime.model"] == "iPhone 15 Pro"
    assert report.location_present is True
    # The coordinates appear nowhere in the report.
    assert "-23.5505" not in report.model_dump_json()


def test_a_file_without_location_says_so():
    report = probe_report_from_ffprobe({"format": {"tags": {"encoder": "x"}}, "streams": []})
    assert report.location_present is False


def test_stripping_again_keeps_the_presence_flag():
    # The orchestrator re-strips what arrives; a report the worker already
    # cleaned must keep location_present=True.
    cleaned = probe_report_from_ffprobe(IPHONE_HDR)
    assert strip_location_tags(cleaned).location_present is True
    # And a report a misbehaving worker sent with coordinates is cleaned.
    dirty = ProbeReport(format=ANDROID_VFR["format"], streams=ANDROID_VFR["streams"])
    assert "location" not in strip_location_tags(dirty).format["tags"]


def test_an_iphone_hdr_video_is_summarised():
    details = summarize_probe_report(probe_report_from_ffprobe(IPHONE_HDR))
    assert details.container == "QuickTime / MOV"
    assert details.duration_s == 31.533333
    assert details.size_bytes == 71234567
    assert (details.video_codec, details.video_profile) == ("hevc", "Main 10")
    assert (details.width, details.height, details.display_aspect) == (1920, 1080, "16:9")
    # Portrait phone video: the display matrix says rotate by -90°.
    assert details.rotation == -90
    assert details.frame_rate == 30.0 and details.variable_frame_rate is False
    # HLG transfer = HDR (the badge the reviewer sees).
    assert details.dynamic_range == "HDR"
    assert (details.audio_codec, details.audio_channels, details.audio_sample_rate) == (
        "aac",
        2,
        44100,
    )
    # Apple's local creation date wins over the UTC container time.
    assert details.creation_time == "2026-11-04T14:31:02-0300"
    assert details.device == "Apple iPhone 15 Pro (iOS 17.5)"
    assert details.location_present is True
    # Tags with their own row are not repeated in "all tags"; others are.
    assert "format:com.apple.quicktime.model" not in details.other_tags
    assert details.other_tags["video 0:handler_name"] == "Core Media Video"


def test_an_android_video_with_a_variable_frame_rate_is_summarised():
    details = summarize_probe_report(probe_report_from_ffprobe(ANDROID_VFR))
    assert details.device == "samsung SM-S918B (Android 14)"
    # Average and nominal rates differ: the phone recorded VFR.
    assert details.variable_frame_rate is True
    assert details.dynamic_range == "SDR"
    assert details.audio_codec is None


def test_an_encoder_tag_is_used_when_there_is_no_phone():
    probe = {"format": {"tags": {"encoder": "Lavf60.16.100"}}, "streams": []}
    details = summarize_probe_report(probe_report_from_ffprobe(probe))
    assert details.device is None
    assert details.encoder == "Lavf60.16.100"
    # No video stream: no HDR/SDR badge rather than a wrong one.
    assert details.dynamic_range is None


def test_an_empty_report_never_fails():
    details = summarize_probe_report(ProbeReport())
    assert details.container is None and details.other_tags == {}
