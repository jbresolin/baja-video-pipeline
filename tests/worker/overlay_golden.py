"""The overlay's golden image: the fixed job it shows, and how to compare and refresh it.

    uv run python tests/worker/overlay_golden.py     # rewrite the golden PNG

Only run that after a deliberate change of the look, then open the PNG,
check it by eye (docs/img/overlay-side-by-side.png shows it next to
BajaOvrLay's own screenshot) and commit it.
"""

from pathlib import Path

from PIL import Image, ImageChops

from worker.overlay import OverlayText, render_overlay_png

GOLDEN_PATH = Path(__file__).parent / "golden" / "overlay-car1-1080p.png"
# Exactly what the orchestrator sends for car 1 of the fake results API
# (BAJA UFMG: short name "Baja UFMG", school_short "UFMG"), Enduro pass 3.
# tests/orchestrator/test_results_sync.py checks that the render task really
# produces this text, so the golden image is the synced data, end to end.
GOLDEN_TEXT = OverlayText(
    prova="Enduro",
    car_number=1,
    team_name="Baja UFMG",
    passagem=3,
    show_pass=True,
    school_short="UFMG",
)
# A channel differing by more than this counts the pixel as different.
CHANNEL_TOLERANCE = 32


def render_golden(destination: Path) -> Image.Image:
    render_overlay_png(destination, GOLDEN_TEXT, frame_width=1920, frame_height=1080)
    return Image.open(destination).convert("RGBA")


def count_differing_pixels(first: Image.Image, second: Image.Image) -> int:
    """Pixels where any channel (alpha included) differs by more than the tolerance."""
    difference = ImageChops.difference(first.convert("RGBA"), second.convert("RGBA"))
    worst_channel = difference.split()
    combined = worst_channel[0]
    for channel in worst_channel[1:]:
        combined = ImageChops.lighter(combined, channel)
    histogram = combined.histogram()
    return sum(histogram[CHANNEL_TOLERANCE + 1 :])


if __name__ == "__main__":
    GOLDEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    render_golden(GOLDEN_PATH)
    print(f"wrote {GOLDEN_PATH}")
