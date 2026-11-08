"""The overlay's golden images: the fixed job they show, and how to compare and refresh them.

    uv run python tests/worker/overlay_golden.py     # rewrite the golden PNGs

Only run that after a deliberate change of the look, then open the PNG,
check it by eye (docs/img/overlay-side-by-side.png shows it next to
BajaOvrLay's own screenshot) and commit it.
"""

from dataclasses import replace
from pathlib import Path

from PIL import Image, ImageChops

from shared.models import RenderClock
from worker.overlay import OverlayText, lay_out_overlay, render_overlay_png
from worker.overlay_clock import compose_clock

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


# The result pill (LIVESTACK-47): four full overlays (name + role +
# result) at 1920x1080, prova "Manobrabilidade". The first three result
# lines are what the orchestrator builds from the real results data in
# tests/fixtures/results_api/26BR_MAN*.json (the orchestrator's tests check
# it produces exactly these strings), so the golden images show real data.
RESULT_CLEAN = "59 s"  # car 26, passagem 1: no penalties
RESULT_CONES = "66 s · 4 cones → 86 s"  # car 26, passagem 2
RESULT_CONES_GATES = "58 s · 8 cones, 2 saídas de pista → 118 s"  # car 2, passagem 1
# Not from the data: a line too wide for the pill at full size, so it is
# drawn a step smaller (95 %) but still whole. Cutting with an ellipsis,
# past 75 %, is checked on a longer line (test_overlay_result.py).
RESULT_LONG = (
    "109 s · 17 cones, 1 saída de pista, 2 toques na barreira de pneus, "
    "1 queima de largada, 3 advertências do fiscal → 204 s"
)
CAR_26_TEXT = OverlayText(
    prova="Manobrabilidade",
    car_number=26,
    team_name="Saci Baja",
    passagem=1,
    show_pass=True,
    school_short="UNIFEI",
)
RESULT_GOLDEN_CASES: dict[str, tuple[OverlayText, Path]] = {
    "clean": (
        replace(CAR_26_TEXT, pass_result=RESULT_CLEAN),
        GOLDEN_PATH.with_name("overlay-result-clean-1080p.png"),
    ),
    "cones": (
        replace(CAR_26_TEXT, passagem=2, pass_result=RESULT_CONES),
        GOLDEN_PATH.with_name("overlay-result-cones-1080p.png"),
    ),
    "cones-gates": (
        replace(
            CAR_26_TEXT,
            car_number=2,
            team_name="FEI",
            school_short="FEI",
            pass_result=RESULT_CONES_GATES,
        ),
        GOLDEN_PATH.with_name("overlay-result-cones-gates-1080p.png"),
    ),
    "long": (
        replace(
            CAR_26_TEXT,
            car_number=47,
            team_name="Equipe Exemplo",
            school_short="UFX",
            pass_result=RESULT_LONG,
        ),
        GOLDEN_PATH.with_name("overlay-result-long-1080p.png"),
    ),
}


# The running clock (LIVESTACK-48): the overlay as a frame of the clip
# shows it, the clock drawn with Pillow (worker.overlay_clock.compose_clock;
# the render writes the same text with ffmpeg's drawtext, in the same font,
# colour and place, minus Pillow's 0.02 em tracking). Car 26's second
# Manobrabilidade run
# ("66 s · 4 cones → 86 s"), official time 66 s with one decimal: at the
# largada (0,0, the tens place blank), mid-run (33,4) and frozen (66,0;
# every frame after the freeze is this same picture). "alone": the result
# toggle off, so the clock takes the result pill's place at the left edge.
CLOCK_66 = RenderClock(largada_s=12.345, official_units=660, decimals=1)
CLOCK_TEXT = replace(CAR_26_TEXT, passagem=2, pass_result=RESULT_CONES, clock=CLOCK_66)
CLOCK_GOLDEN_CASES: dict[str, tuple[OverlayText, int, Path]] = {
    "start": (CLOCK_TEXT, 0, GOLDEN_PATH.with_name("overlay-clock-start-1080p.png")),
    "mid": (CLOCK_TEXT, 334, GOLDEN_PATH.with_name("overlay-clock-mid-1080p.png")),
    "frozen": (CLOCK_TEXT, 660, GOLDEN_PATH.with_name("overlay-clock-frozen-1080p.png")),
    "alone": (
        replace(CLOCK_TEXT, pass_result=None),
        334,
        GOLDEN_PATH.with_name("overlay-clock-alone-1080p.png"),
    ),
}


def render_clock_frame(text: OverlayText, units: int) -> Image.Image:
    """The still overlay with the clock at `units`, as one transparent 1920x1080 frame.

    The result pill is drawn only once the clock shows the official time:
    with a clock, the result appears when it stops (LIVESTACK-53).
    """
    layout = lay_out_overlay(text, frame_width=1920, frame_height=1080)
    canvas = layout.still.copy()
    canvas.alpha_composite(
        compose_clock(layout.clock, units, text.clock.decimals), dest=layout.clock.region[:2]
    )
    if layout.result is not None and units == text.clock.official_units:
        canvas.alpha_composite(layout.result.image, dest=layout.result.position)
    return canvas


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
    for text, path in [*GOLDEN_CASES.values(), *RESULT_GOLDEN_CASES.values()]:
        render_golden(path, text)
        print(f"wrote {path}")
    for text, units, path in CLOCK_GOLDEN_CASES.values():
        render_clock_frame(text, units).save(path, format="PNG")
        print(f"wrote {path}")
