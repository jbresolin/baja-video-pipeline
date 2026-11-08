"""Golden frames of a vertical video with the clock and the result (LIVESTACK-53).

A video filmed upright is fitted into the 1920×1080 clip (design §29):
blurred copy behind it ("desfocado") or black bars ("barras pretas"). The
overlay is always drawn on the whole 16:9 frame, so the clock still sits
in the frame's top-right corner, over the blur or the bar, not over the
upright picture. These frames show that, for João to check by eye.

How a frame is made: an upright test picture (drawn here with Pillow: sky,
grass, a track, a car-sized box) goes through the render's own picture
filters (shared.ffmpeg_picture.picture_filters, the same plan the worker
uses), then the still overlay, the clock pill with its digits by drawtext
(frozen on 66,0) and the result pill, exactly as in the render, but
written straight to PNG instead of being encoded, so the picture does not
depend on an encoder.

    uv run python tests/worker/overlay_golden.py     # rewrites these too (needs ffmpeg)
"""

import subprocess
from pathlib import Path

from PIL import Image, ImageDraw

from shared.ffmpeg import DEFAULT_FINAL_RENDER_SETTINGS
from shared.ffmpeg_clock import drawtext_filter
from shared.ffmpeg_picture import picture_filters
from shared.framing import plan_frame
from shared.models import Framing
from shared.pass_clock import clock_text
from worker.overlay import OverlayText, render_overlay
from worker.overlay_clock import write_clock_pill

GOLDEN_DIR = Path(__file__).parent / "golden"
# An upright phone video, 9:16.
SOURCE_SIZE = (720, 1280)
FRAMING_GOLDEN_CASES: dict[str, tuple[Framing, Path]] = {
    "blurred": (Framing.BLURRED, GOLDEN_DIR / "overlay-clock-vertical-blurred-1080p.png"),
    "bars": (Framing.BLACK_BARS, GOLDEN_DIR / "overlay-clock-vertical-bars-1080p.png"),
}


def draw_source_picture(destination: Path) -> Path:
    """An upright test picture: sky, a treeline, grass, a dirt track and a box on it."""
    width, height = SOURCE_SIZE
    picture = Image.new("RGB", SOURCE_SIZE, (120, 170, 220))
    draw = ImageDraw.Draw(picture)
    draw.rectangle((0, 380, width, 470), fill=(40, 90, 50))
    draw.rectangle((0, 470, width, height), fill=(90, 150, 70))
    draw.polygon([(260, 470), (460, 470), (width, height), (0, height)], fill=(150, 115, 80))
    draw.rectangle((300, 780, 430, 880), fill=(220, 120, 30))
    picture.save(destination, format="PNG")
    return destination


def render_framing_frame(directory: Path, framing: Framing, text: OverlayText) -> Image.Image:
    """One frame of the clip: the upright picture framed, the overlay, the frozen clock."""
    directory.mkdir(parents=True, exist_ok=True)
    source = draw_source_picture(directory / "upright.png")
    overlay_png = directory / "overlay.png"
    layout = render_overlay(overlay_png, text, frame_width=1920)
    clock = write_clock_pill(
        layout.clock, text.clock, 0.0, directory, stem="frame", result=layout.result
    )
    plan = plan_frame(*SOURCE_SIZE, framing=framing)
    picture = picture_filters(
        plan, height=1080, tone_map=None, blur=DEFAULT_FINAL_RENDER_SETTINGS.blur
    )
    frozen = clock_text(text.clock.official_units, text.clock.decimals)
    pill_x, pill_y = clock.pill_position
    result_x, result_y = clock.result_position
    digits = drawtext_filter(clock, frozen, None)
    steps = [
        *picture[:-1],
        f"{picture[-1]}[base]",
        "[base][1:v]overlay=0:0[lower_third]",
        f"[lower_third][2:v]overlay={pill_x}:{pill_y},{digits}[clock]",
        f"[clock][3:v]overlay={result_x}:{result_y},format=rgb24",
    ]
    frame = directory / f"frame-{framing.value}.png"
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-y",
            "-i", str(source), "-i", str(overlay_png),
            "-i", str(clock.pill_png), "-i", str(clock.result_png),
            "-filter_complex", ";".join(steps), "-frames:v", "1", str(frame),
        ],
        check=True,
    )  # fmt: skip
    return Image.open(frame).convert("RGBA")
