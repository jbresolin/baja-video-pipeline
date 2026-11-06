"""The overlay's golden images: the fixed job they show, and how to compare and refresh them.

    uv run python tests/worker/overlay_golden.py     # rewrite the golden PNGs

Only run that after a deliberate change of the look, then open the PNG,
check it by eye (docs/img/overlay-side-by-side.png shows it next to
BajaOvrLay's own screenshot) and commit it.
"""

from dataclasses import replace
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

# Car 2 under the four name choices of the Settings page (LIVESTACK-42):
# team full/short × school full/sigla. Keys are the
# (clip_team_name_style, clip_school_name_style) values. Car 2 of the fake
# results API, because all four of its names differ even in capitals (car
# 1's "Baja UFMG" and "BAJA UFMG" look the same on the overlay):
# "Equipe FEI Baja" / "FEI", "Fundação Educacional Inaciana - FEI" / "FEI".
# tests/orchestrator/test_clip_names.py checks that each setting really
# produces this text.
CAR_2_TEXT = OverlayText(prova="Enduro", car_number=2, team_name=None, passagem=3, show_pass=True)
CAR_2_TEAM = {"full": "Equipe FEI Baja", "short": "FEI"}
CAR_2_SCHOOL = {"full": "Fundação Educacional Inaciana - FEI", "short": "FEI"}
SCHOOL_STYLE_IN_FILE_NAME = {"full": "full", "short": "sigla"}
GOLDEN_CASES: dict[tuple[str, str], tuple[OverlayText, Path]] = {
    (team_style, school_style): (
        replace(
            CAR_2_TEXT,
            team_name=CAR_2_TEAM[team_style],
            school_short=CAR_2_SCHOOL[school_style],
        ),
        GOLDEN_PATH.with_name(
            f"overlay-car2-team-{team_style}"
            f"-school-{SCHOOL_STYLE_IN_FILE_NAME[school_style]}-1080p.png"
        ),
    )
    for team_style in ("full", "short")
    for school_style in ("full", "short")
}


def golden_case_id(choice: tuple[str, str]) -> str:
    """A readable test id for a GOLDEN_CASES key, e.g. "team-full-school-short"."""
    team_style, school_style = choice
    return f"team-{team_style}-school-{school_style}"


def render_golden(destination: Path, text: OverlayText = GOLDEN_TEXT) -> Image.Image:
    render_overlay_png(destination, text, frame_width=1920, frame_height=1080)
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
    for text, path in GOLDEN_CASES.values():
        render_golden(path, text)
        print(f"wrote {path}")
