"""Tests for shared.framing: which way up a video is, and how it fills 1920×1080 (LIVESTACK-50).

Background for a beginner: a phone filmed upright usually stores its
frames lying down (1920 wide, 1080 high) and adds a note saying "turn me
90°". Players, and ffmpeg, obey the note. So the size that matters is the
size *after* the note: that is what these functions work out from
ffprobe's answer, and what decides whether the clip needs side fill.
"""

import json

import pytest

from shared.framing import (
    FramePlan,
    display_size,
    display_size_from_probe,
    effective_framing,
    fills_frame_like_before,
    is_vertical,
    needs_framing,
    plan_frame,
    rotation_of_video_stream,
)
from shared.media_probe import parse_probe_output
from shared.models import Framing

# --- The three files the brief names, as ffprobe describes them (trimmed). ---

# An iPhone filmed upright: frames stored 1920×1080, plus the display matrix
# side data. ffprobe reports that rotation counterclockwise: -90 = "turn 90°
# clockwise".
PORTRAIT_PHONE_STREAMS = [
    {
        "codec_type": "video",
        "width": 1920,
        "height": 1080,
        "side_data_list": [{"side_data_type": "Display Matrix", "rotation": -90}],
    },
    {"codec_type": "audio"},
]
# A file stored upright: 1080×1920 frames and no rotation note at all
# (what some apps and screen recorders write).
STORED_PORTRAIT_STREAMS = [{"codec_type": "video", "width": 1080, "height": 1920}]
# An ordinary landscape video.
LANDSCAPE_STREAMS = [{"codec_type": "video", "width": 1920, "height": 1080}]


@pytest.mark.parametrize(
    ("streams", "expected_size", "expected_vertical"),
    [
        (PORTRAIT_PHONE_STREAMS, (1080, 1920), True),
        (STORED_PORTRAIT_STREAMS, (1080, 1920), True),
        (LANDSCAPE_STREAMS, (1920, 1080), False),
    ],
    ids=["portrait-phone-side-data", "stored-portrait-no-rotation", "landscape"],
)
def test_the_probe_gives_the_size_players_show_and_the_orientation(
    streams, expected_size, expected_vertical
):
    # What it does: read the first video stream's size and rotation note.
    size = display_size_from_probe(streams)

    # The phone's 1920×1080 frames become 1080×1920 once turned, exactly
    # like the file stored upright; the landscape one stays as it is.
    assert size == expected_size
    assert is_vertical(*size) is expected_vertical


def test_media_info_from_ffprobe_carries_the_rotation_and_the_shown_size():
    # The worker's own probe (shared.media_probe) reads the same note, so the
    # render and the orchestrator agree on the shape of the picture.
    info = parse_probe_output(json.dumps({"format": {}, "streams": PORTRAIT_PHONE_STREAMS}))
    assert (info.width, info.height) == (1920, 1080)  # as stored
    assert info.rotation == 90  # clockwise
    assert info.display_size == (1080, 1920)  # as shown


@pytest.mark.parametrize(
    ("stream", "clockwise"),
    [
        ({"side_data_list": [{"rotation": -90}]}, 90),
        ({"side_data_list": [{"rotation": 90}]}, 270),
        ({"side_data_list": [{"rotation": 180}]}, 180),
        ({"side_data_list": [{"rotation": -180.0}]}, 180),
        # Older files: the `rotate` tag, already clockwise.
        ({"tags": {"rotate": "90"}}, 90),
        ({"tags": {"rotate": "270"}}, 270),
        # Side data wins over the tag (it is what ffmpeg obeys).
        ({"side_data_list": [{"rotation": -90}], "tags": {"rotate": "0"}}, 90),
        # No note, or one that is not a number: not turned.
        ({}, 0),
        ({"tags": {"rotate": "sideways"}}, 0),
        ({"side_data_list": [{"side_data_type": "Mastering display metadata"}]}, 0),
    ],
)
def test_rotation_notes_are_read_as_clockwise_quarter_turns(stream, clockwise):
    assert rotation_of_video_stream(stream) == clockwise


def test_no_video_stream_or_no_size_means_unknown():
    assert display_size_from_probe([{"codec_type": "audio"}]) is None
    assert display_size_from_probe([{"codec_type": "video"}]) is None
    assert is_vertical(None, None) is None


def test_a_quarter_turn_swaps_width_and_height():
    assert display_size(1920, 1080, 90) == (1080, 1920)
    assert display_size(1920, 1080, 270) == (1080, 1920)
    assert display_size(1920, 1080, 180) == (1920, 1080)


# --- 16:9 or not: the same question ffmpeg's scale=-2:1080 answers ---


@pytest.mark.parametrize(
    ("width", "height", "sixteen_nine"),
    [
        (1920, 1080, True),
        (1280, 720, True),
        (3840, 2160, True),
        (854, 480, False),  # "480p" is 1.779:1: scale=-2:1080 would give 1922 wide
        (1920, 1088, False),  # scale=-2:1080 would give 1906 wide
        (1440, 1080, False),  # 4:3
        (2560, 1080, False),  # ultra-wide
        (1080, 1920, False),  # upright
    ],
)
def test_only_pictures_the_old_render_made_1920_wide_count_as_16_9(width, height, sixteen_nine):
    assert fills_frame_like_before(width, height) is sixteen_nine


# --- The plan: what the render does with each kind of picture ---


def test_a_16_9_video_gets_no_framing_whatever_was_chosen():
    # Framing only applies to pictures that are not 16:9, so a landscape
    # video renders exactly as before LIVESTACK-50.
    for framing in Framing:
        assert plan_frame(1920, 1080, framing=framing) == FramePlan(0, 1920, 1080, framing=None)


def test_an_upright_video_is_fitted_at_full_height_in_the_middle():
    plan = plan_frame(1080, 1920)
    # Default framing is the blurred fill ("desfocado", João's choice).
    assert plan.framing == Framing.BLURRED
    # 1080×1920 scaled to 1080 high is 608 wide (607.5 rounded to even),
    # placed with (1920 − 608) / 2 = 656 px on each side.
    assert plan.fitted_size == (608, 1080)
    assert plan.fitted_offset == (656, 0)


def test_a_4_3_video_is_fitted_at_full_height_and_an_ultra_wide_one_at_full_width():
    four_three = plan_frame(1440, 1080, framing=Framing.BLACK_BARS)
    assert four_three.fitted_size == (1440, 1080)
    assert four_three.fitted_offset == (240, 0)
    ultra_wide = plan_frame(2560, 1080, framing=Framing.BLACK_BARS)
    # 2560×1080 scaled to 1920 wide is 810 high: bars above and below.
    assert ultra_wide.fitted_size == (1920, 810)
    assert ultra_wide.fitted_offset == (0, 135)


def test_cortar_takes_a_full_width_16_9_band_that_slides_up_and_down():
    # A 1080×1920 picture: the band is 1080 wide and 608 high (1080 × 9/16),
    # and can sit anywhere in the 1920 − 608 = 1312 rows left over.
    top = plan_frame(1080, 1920, framing=Framing.CROP, crop_position=0.0)
    middle = plan_frame(1080, 1920, framing=Framing.CROP, crop_position=0.5)
    bottom = plan_frame(1080, 1920, framing=Framing.CROP, crop_position=1.0)
    assert top.crop_box == (1080, 608, 0, 0)
    assert middle.crop_box == (1080, 608, 0, 656)
    assert bottom.crop_box == (1080, 608, 0, 1312)


def test_cortar_on_an_ultra_wide_picture_takes_a_full_height_band_that_slides_sideways():
    plan = plan_frame(2560, 1080, framing=Framing.CROP, crop_position=0.0)
    assert plan.crop_box == (1920, 1080, 0, 0)
    plan = plan_frame(2560, 1080, framing=Framing.CROP, crop_position=1.0)
    assert plan.crop_box == (1920, 1080, 640, 0)


def test_the_crop_position_is_kept_only_for_cortar():
    assert plan_frame(1080, 1920, framing=Framing.BLURRED, crop_position=0.2).crop_position is None
    assert plan_frame(1080, 1920, framing=Framing.CROP, crop_position=0.2).crop_position == 0.2


def test_girar_turns_the_picture_before_anything_else():
    # A phone held sideways whose note wrongly says "upright": ffmpeg shows
    # it 1080×1920, the reviewer turns it 90°, and it is 16:9 again, so
    # no framing at all: only the turn.
    plan = plan_frame(1080, 1920, rotation=90, framing=Framing.BLACK_BARS)
    assert plan == FramePlan(90, 1920, 1080, framing=None)
    # The other way round: a landscape video turned becomes upright.
    plan = plan_frame(1920, 1080, rotation=270)
    assert (plan.rotation, plan.width, plan.height, plan.framing) == (
        270,
        1080,
        1920,
        Framing.BLURRED,
    )


# --- What the orchestrator compares and gates on ---


def test_effective_framing_is_none_for_a_16_9_picture_and_unknown_sizes():
    # On a 16:9 picture "barras pretas" changes nothing on screen: nothing
    # to compare, so the clip on air is never marked out of date for it.
    assert effective_framing(
        1920, 1080, rotation=0, framing=Framing.BLACK_BARS, crop_position=0.5
    ) == (None, None)
    assert effective_framing(
        None, None, rotation=0, framing=Framing.BLACK_BARS, crop_position=0.5
    ) == (None, None)
    assert effective_framing(1080, 1920, rotation=0, framing=Framing.CROP, crop_position=0.25) == (
        Framing.CROP,
        0.25,
    )


def test_needs_framing_for_a_turn_or_a_picture_that_is_not_16_9():
    assert needs_framing(1920, 1080, 0) is False
    assert needs_framing(1920, 1080, 180) is True  # any turn is new
    assert needs_framing(1080, 1920, 0) is True
    assert needs_framing(1440, 1080, 0) is True
    # Size unknown and no turn: a worker of any version renders it as before.
    assert needs_framing(None, None, 0) is False
