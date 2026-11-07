"""The final render's picture steps: turn, then fit into 1920×1080 (LIVESTACK-50).

shared.ffmpeg.build_final_render_command asks picture_filters() for the
`-filter_complex` entries that take the raw's video ([0:v]) to the finished
1920×1080 picture, before the overlay. The numbers come from a
shared.framing.FramePlan; nothing here decides, it only writes the plan
down as ffmpeg filters, so a test can read every step.
"""

from dataclasses import dataclass

from shared.framing import OUTPUT_HEIGHT, OUTPUT_WIDTH, FramePlan
from shared.models.enums import Framing

# The reviewer's extra clockwise turn ("Girar"), as ffmpeg filters.
# transpose=clock turns 90° clockwise; a half turn is both flips.
ROTATION_FILTERS = {90: "transpose=clock", 180: "hflip,vflip", 270: "transpose=cclock"}


@dataclass(frozen=True)
class BlurFill:
    """How the blurred sides (Framing.BLURRED) are made.

    On a copy this small, then scaled up: blurring 480×270 costs almost
    nothing, and once scaled up it looks the same as blurring the full frame.
    """

    canvas_width: int = 480
    canvas_height: int = 270
    # boxblur radius and passes on that small copy.
    radius: int = 12
    passes: int = 2
    # Covered with black this opaque (0 = untouched, 1 = black), so the real
    # picture in front stands out. A translucent black box works at any bit
    # depth (HDR raws are 10-bit until tone mapping).
    darkening: float = 0.45


def picture_filters(
    plan: FramePlan | None, *, height: int, tone_map: str | None, blur: BlurFill
) -> list[str]:
    """filter_complex entries from [0:v] to the 1920×1080 picture.

    The last entry has no output label, so the caller can chain the
    overlay or the pixel format onto it. `tone_map` (the HDR → SDR filters,
    or None) runs last, on the 1080p picture, as before (cheaper than on
    the full-size raw). `height` is the old render's output height.
    """
    turn = [ROTATION_FILTERS[plan.rotation]] if plan and plan.rotation else []
    tone = [tone_map] if tone_map else []
    if plan is None or plan.framing is None:
        # 16:9: the render from before LIVESTACK-50, plus a turn if asked.
        return ["[0:v]" + ",".join([*turn, f"scale=-2:{height}", *tone])]
    if plan.framing == Framing.CROP:
        width, band_height, x, y = plan.crop_box
        steps = [
            *turn,
            f"crop={width}:{band_height}:{x}:{y}",
            f"scale={OUTPUT_WIDTH}:{OUTPUT_HEIGHT}",
            "setsar=1",
            *tone,
        ]
        return ["[0:v]" + ",".join(steps)]
    fitted_width, fitted_height = plan.fitted_size
    left, top = plan.fitted_offset
    if plan.framing == Framing.BLACK_BARS:
        steps = [
            *turn,
            f"scale={fitted_width}:{fitted_height}",
            f"pad={OUTPUT_WIDTH}:{OUTPUT_HEIGHT}:{left}:{top}:color=black",
            "setsar=1",
            *tone,
        ]
        return ["[0:v]" + ",".join(steps)]
    # Framing.BLURRED: two copies of the picture. The background copy is
    # shrunk to a small 16:9 canvas (cover, centre-cropped), blurred,
    # darkened and scaled back up; the foreground copy is fitted whole and
    # laid on top in the middle. The background's scaling uses the cheap
    # bilinear filters: nobody can tell on a blur, and ffmpeg's default
    # (bicubic) upscale to 1920×1080 was the costliest step of the render.
    split = "[0:v]" + ",".join([*turn, "split=2[fill][front]"])
    background = (
        f"[fill]scale={blur.canvas_width}:{blur.canvas_height}"
        ":force_original_aspect_ratio=increase:flags=fast_bilinear,"
        f"crop={blur.canvas_width}:{blur.canvas_height},"
        f"boxblur={blur.radius}:{blur.passes},"
        f"drawbox=color=black@{blur.darkening}:thickness=fill,"
        f"scale={OUTPUT_WIDTH}:{OUTPUT_HEIGHT}:flags=bilinear,setsar=1[blurred]"
    )
    foreground = f"[front]scale={fitted_width}:{fitted_height},setsar=1[fitted]"
    # format=auto keeps a 10-bit (HDR) picture 10-bit until tone mapping.
    combined = ",".join([f"[blurred][fitted]overlay=x={left}:y={top}:format=auto", *tone])
    return [split, background, foreground, combined]
