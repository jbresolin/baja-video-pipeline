"""Which way up a video is, and how it fills the 1920×1080 clip (LIVESTACK-50).

    rotation = rotation_of_video_stream(stream)            # from ffprobe
    width, height = display_size(stream["width"], stream["height"], rotation)
    plan = plan_frame(width, height, rotation=90, framing=Framing.BLURRED)
    plan.framing        # None: 16:9 already, rendered exactly as before

Every clip is 1920×1080 whatever the source. A phone filmed upright stores
its frames lying down (1920×1080) plus a note saying "turn me 90°" (the
display matrix side data, or the older `rotate` tag); players and ffmpeg
obey the note, so what matters is the size *after* it. On top of that the
reviewer may turn the picture again ("Girar", for a phone whose note is
wrong). Whatever comes out of those two turns:

* is 16:9 → the render is exactly the one used before LIVESTACK-50
  (`scale=-2:1080`), so horizontal videos keep their look to the pixel;
* is anything else (upright phone, 4:3, ultra-wide) → it is fitted at full
  height or full width and the rest of the frame is filled as the
  reviewer chose (Framing): blurred copy, black, or a cropped 16:9 band.

The numbers are worked out here, in Python, so the ffmpeg filter graph
(shared.ffmpeg) holds plain integers a test can read.
"""

import math
from dataclasses import dataclass
from typing import Any

from shared.models.enums import Framing

OUTPUT_WIDTH = 1920
OUTPUT_HEIGHT = 1080
# The clockwise turns a reviewer can choose ("Girar").
ROTATIONS = (0, 90, 180, 270)
# Where a cropped band sits when nobody chose: the middle.
DEFAULT_CROP_POSITION = 0.5


def _round_half_up(value: float) -> int:
    """2.5 → 3, as ffmpeg and JavaScript's Math.round do (Python's round gives 2)."""
    return math.floor(value + 0.5)


def _even_offset(value: float) -> int:
    """Rounded to the nearest even number, 0 included (4:2:0 colour crops on even rows)."""
    return 2 * _round_half_up(value / 2)


def _even(value: float) -> int:
    """A size rounded to the nearest even number, at least 2 (H.264 4:2:0 needs even sizes)."""
    return max(2, _even_offset(value))


def _degrees(value: Any) -> int | None:
    try:
        return _round_half_up(float(value))
    except (TypeError, ValueError):
        return None


def normalize_rotation(degrees: int) -> int:
    """Any angle → the nearest of 0, 90, 180, 270 (clockwise)."""
    return (_round_half_up(degrees / 90) * 90) % 360


def rotation_of_video_stream(stream: dict[str, Any]) -> int:
    """How far a player turns this stream clockwise: 0, 90, 180 or 270.

    The display matrix (`side_data_list[].rotation`) is what recent phones
    write and ffmpeg obeys; ffprobe reports it counterclockwise, so -90
    means "turn 90° clockwise". Older files have a `rotate` tag instead,
    which is already clockwise. Neither → 0.
    """
    for side_data in stream.get("side_data_list") or []:
        if isinstance(side_data, dict) and "rotation" in side_data:
            degrees = _degrees(side_data["rotation"])
            if degrees is not None:
                return normalize_rotation(-degrees)
    degrees = _degrees((stream.get("tags") or {}).get("rotate"))
    return normalize_rotation(degrees) if degrees is not None else 0


def display_size(width: int, height: int, rotation: int) -> tuple[int, int]:
    """(width, height) after a clockwise turn: a quarter turn swaps them."""
    if normalize_rotation(rotation) in (90, 270):
        return height, width
    return width, height


def display_size_from_probe(streams: list[dict[str, Any]]) -> tuple[int, int] | None:
    """The first video stream's picture as players show it, or None without one."""
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None:
        return None
    width, height = video.get("width"), video.get("height")
    if not isinstance(width, int) or not isinstance(height, int) or width <= 0 or height <= 0:
        return None
    return display_size(width, height, rotation_of_video_stream(video))


def is_vertical(width: int | None, height: int | None) -> bool | None:
    """Taller than wide; None when the size is not known."""
    if not width or not height:
        return None
    return height > width


def fills_frame_like_before(width: int, height: int) -> bool:
    """True when `scale=-2:1080` (the render before LIVESTACK-50) gives exactly 1920×1080.

    That is the 16:9 test. ffmpeg's `-2` rounds the width to the nearest
    even number, so this asks the same question ffmpeg answers: 1280×720,
    1920×1080 and 3840×2160 pass; 1920×1088 (a 1906-wide result) does not.
    """
    scaled_width = 2 * _round_half_up(width * OUTPUT_HEIGHT / (2 * height))
    return scaled_width == OUTPUT_WIDTH


@dataclass(frozen=True)
class FramePlan:
    """What the final render does to the picture, before the overlay.

    `rotation` is the reviewer's extra clockwise turn (ffmpeg has already
    applied the file's own note). `width`/`height` are the picture after
    both turns. `framing` is None when that picture is 16:9: then nothing
    but the old `scale=-2:1080` happens. Otherwise the numbers below say
    where things go in the 1920×1080 frame. Either way the clip is exactly
    OUTPUT_WIDTH × OUTPUT_HEIGHT.
    """

    rotation: int
    width: int
    height: int
    framing: Framing | None
    # Only for Framing.CROP: 0 = the band at the top (or left), 1 = bottom
    # (or right), 0.5 = middle. None for every other framing.
    crop_position: float | None = None

    @property
    def fitted_size(self) -> tuple[int, int]:
        """The whole picture scaled to fit inside 1920×1080 (blurred, black bars)."""
        scale = min(OUTPUT_WIDTH / self.width, OUTPUT_HEIGHT / self.height)
        return (
            min(OUTPUT_WIDTH, _even(self.width * scale)),
            min(OUTPUT_HEIGHT, _even(self.height * scale)),
        )

    @property
    def fitted_offset(self) -> tuple[int, int]:
        """Top-left corner of the fitted picture, centred in the frame."""
        fitted_width, fitted_height = self.fitted_size
        return (OUTPUT_WIDTH - fitted_width) // 2, (OUTPUT_HEIGHT - fitted_height) // 2

    @property
    def crop_box(self) -> tuple[int, int, int, int]:
        """(width, height, x, y) of the 16:9 band cut out of the picture (Framing.CROP).

        A picture taller than 16:9 gives a full-width band that slides up
        and down; a wider one a full-height band that slides sideways.
        """
        position = DEFAULT_CROP_POSITION if self.crop_position is None else self.crop_position
        if self.width * OUTPUT_HEIGHT < self.height * OUTPUT_WIDTH:
            band_width = self.width - self.width % 2
            band_height = min(self.height - self.height % 2, _even(self.width * 9 / 16))
            top = _even_offset((self.height - band_height) * position)
            return band_width, band_height, 0, min(top, self.height - band_height)
        band_height = self.height - self.height % 2
        band_width = min(self.width - self.width % 2, _even(self.height * 16 / 9))
        left = _even_offset((self.width - band_width) * position)
        return band_width, band_height, min(left, self.width - band_width), 0


def plan_frame(
    width: int,
    height: int,
    *,
    rotation: int = 0,
    framing: Framing = Framing.BLURRED,
    crop_position: float = DEFAULT_CROP_POSITION,
) -> FramePlan:
    """The plan for a picture of `width`×`height` (after the file's own rotation note).

    `rotation`, `framing` and `crop_position` are the reviewer's choices
    (ProcessTask). A 16:9 result ignores `framing`.
    """
    rotation = normalize_rotation(rotation)
    turned_width, turned_height = display_size(width, height, rotation)
    if fills_frame_like_before(turned_width, turned_height):
        return FramePlan(rotation, turned_width, turned_height, framing=None)
    position = min(1.0, max(0.0, crop_position)) if framing == Framing.CROP else None
    return FramePlan(rotation, turned_width, turned_height, framing=framing, crop_position=position)


def effective_framing(
    display_width: int | None,
    display_height: int | None,
    *,
    rotation: int,
    framing: Framing,
    crop_position: float,
) -> tuple[Framing | None, float | None]:
    """(framing, crop position) as they affect the picture; (None, None) when they do not.

    Used for the render inputs (LIVESTACK-43): choosing "barras pretas" on
    a 16:9 video changes nothing on screen, so it must not mark the clip on
    air as out of date. Unknown size → (None, None): nothing to compare.
    """
    if not display_width or not display_height:
        return None, None
    plan = plan_frame(
        display_width,
        display_height,
        rotation=rotation,
        framing=framing,
        crop_position=crop_position,
    )
    return plan.framing, plan.crop_position


def needs_framing(display_width: int | None, display_height: int | None, rotation: int) -> bool:
    """True when the render differs from the old one: a turn, or a picture that is not 16:9."""
    if normalize_rotation(rotation) != 0:
        return True
    if not display_width or not display_height:
        return False
    return not fills_frame_like_before(display_width, display_height)
