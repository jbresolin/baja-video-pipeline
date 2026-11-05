"""Tests for the clip overlay PNG (worker.overlay): BajaOvrLay's lower third, drawn still.

Background for a beginner: every finished clip gets a picture burned in
at the bottom-left, saying which car, team, prova and passagem it is. It
must look like the lower third ("Tarja") the live stream shows, so these
tests check where the two "pills" (rounded boxes) land, what text goes in
them, and that the whole picture still matches a reference image that a
human approved (the "golden image").

How the tests find the pills: the PNG is transparent everywhere except
the pills, so a pixel with alpha > 0 belongs to a pill. Walking down one
column of pixels near the left edge shows each pill as a run of opaque
rows; walking right along a row shows where that pill ends.

To refresh the golden image after a deliberate change of the look:
    uv run python tests/worker/overlay_golden.py
then look at the new PNG and commit it.
"""

from PIL import Image

from tests.worker.overlay_golden import (
    GOLDEN_PATH,
    GOLDEN_TEXT,
    count_differing_pixels,
    render_golden,
)
from worker import overlay_tokens as theme
from worker.overlay import (
    OverlayText,
    final_frame_width,
    layout_name_pill,
    render_overlay_png,
)
from worker.overlay_text import ELLIPSIS, TextRun


def pill_rows(image: Image.Image, x: int) -> list[tuple[int, int]]:
    """(first row, last row) of each opaque run in column x, top to bottom."""
    runs, start = [], None
    for y in range(image.height):
        opaque = image.getpixel((x, y))[3] > 0
        if opaque and start is None:
            start = y
        if not opaque and start is not None:
            runs.append((start, y - 1))
            start = None
    if start is not None:
        runs.append((start, image.height - 1))
    return runs


def pill_right_edge(image: Image.Image, y: int) -> int:
    """The last opaque pixel of row y (where the pill on that row ends)."""
    return max(x for x in range(image.width) if image.getpixel((x, y))[3] > 0)


def render(tmp_path, text: OverlayText, width: int = 1920, height: int = 1080) -> Image.Image:
    path = render_overlay_png(
        tmp_path / "overlay.png", text, frame_width=width, frame_height=height
    )
    return Image.open(path)


# --- What the pills say ---


def test_role_pill_parts_are_prova_passagem_and_muted_school():
    text = OverlayText("Enduro", 42, "Equipe Exemplo", 3, show_pass=True, school_short="UFMG")
    # Everything in capitals: Bebas Neue has no lowercase letters.
    assert text.name() == "EQUIPE EXEMPLO"
    # (text, muted): the school is the muted (greyer) part, as on air.
    assert text.role_parts() == [("ENDURO", False), ("PASSAGEM 3", False), ("UFMG", True)]


def test_unknown_parts_are_left_out_and_show_pass_hides_the_passagem():
    # No school known, and the reviewer unticked "show pass".
    text = OverlayText("Suspensão", 7, None, 2, show_pass=False)
    assert text.name() is None
    # Accented letters are upper-cased too (ã → Ã), the face has them.
    assert text.role_parts() == [("SUSPENSÃO", False)]


# --- Where the pills are ---


def test_full_hd_pills_sit_bottom_left_like_the_on_air_lower_third(tmp_path):
    image = render(tmp_path, GOLDEN_TEXT)
    # The canvas is the whole frame (ffmpeg lays it over the video as is).
    assert image.size == (1920, 1080) and image.mode == "RGBA"
    # Column x=100 crosses both pills: name pill 64 px tall, then 3 px gap
    # (--row-gap), then the role pill 40 px tall ending at y 940.
    assert pill_rows(image, 100) == [(833, 896), (900, 939)]
    # Nothing left of x=96 (the title-safe edge).
    assert pill_rows(image, 95) == []
    # The top-left corner is fully transparent: only the pills are drawn.
    assert image.getpixel((0, 0))[3] == 0


def test_the_car_chip_is_the_blue_box_inside_the_name_pill(tmp_path):
    image = render(tmp_path, GOLDEN_TEXT)
    # 8 px inside the name pill's left edge (--space-2), the chip colour.
    # (x 106, y 845: inside the chip but clear of its digits.)
    assert image.getpixel((106, 845)) == theme.CHIP_BACKGROUND
    # The pill itself is the translucent slate (±1 for rounding at blending).
    pill_pixel = image.getpixel((100, 865))
    assert all(abs(a - b) <= 1 for a, b in zip(pill_pixel, theme.PANEL_COLOUR, strict=True))


def test_a_missing_team_draws_the_chip_alone_and_the_role_pill(tmp_path):
    image = render(tmp_path, OverlayText("Enduro", 42, None, 3, show_pass=True))
    # Still two pills: the name pill holds just the chip...
    assert pill_rows(image, 100) == [(833, 896), (900, 939)]
    # ...so it ends 8 px (--space-2) after the blue chip, with no room for
    # a name: the last chip-blue pixel on the row is 8 px before the pill's end.
    pill_end = pill_right_edge(image, 865)
    chip_end = max(
        x for x in range(96, pill_end) if image.getpixel((x, 845)) == theme.CHIP_BACKGROUND
    )
    # 8 px, or 9 when the chip's smoothed edge pixel is not pure blue.
    assert 8 <= pill_end - chip_end <= 9
    assert pill_end < 96 + 100  # just the chip: well under 100 px wide


def test_720p_output_scales_every_size_by_two_thirds(tmp_path):
    image = render(tmp_path, GOLDEN_TEXT, width=1280, height=720)
    # 1080 → 720 is ×2/3: left 96 → 64, bottom 940 → 627, pills 64/40 → 43/27.
    rows = pill_rows(image, 70)
    assert len(rows) == 2
    (name_top, name_bottom), (role_top, role_bottom) = rows
    assert abs(role_bottom + 1 - 940 * 2 / 3) <= 1
    assert abs((role_bottom - role_top + 1) - 40 * 2 / 3) <= 1
    assert abs((name_bottom - name_top + 1) - 64 * 2 / 3) <= 1
    assert pill_rows(image, 63) == []


def test_vertical_output_keeps_the_pills_inside_the_frame(tmp_path):
    # A phone filmed upright renders at 608x1080 (scale=-2:1080).
    width = final_frame_width(1080, 1920)
    assert width == 608
    long_text = OverlayText(
        "Enduro de Resistência", 1234, "Equipe Com Um Nome Muito Comprido Mesmo", 12, True, "UFMG"
    )
    image = render(tmp_path, long_text, width=width)
    # Same height as full HD, so the same rows...
    assert pill_rows(image, 100) == [(833, 896), (900, 939)]
    # ...but no pill may run past the right safe edge (608 − 96).
    assert pill_right_edge(image, 865) <= width - 96
    assert pill_right_edge(image, 920) <= width - 96


def test_frame_width_follows_the_aspect_ratio():
    assert final_frame_width(3840, 2160) == 1920
    assert final_frame_width(None, None) == 1920  # unknown: assume 16:9


# --- Long names: shrink first, then cut ---


def team_runs(name: str) -> list[TextRun]:
    return [TextRun(name, tracking_em=theme.NAME_TRACKING_EM)]


def test_a_name_that_fits_keeps_the_full_font_size():
    pill = layout_name_pill("42", team_runs("BAJA UFMG"), max_width=1300, scale=1.0)
    assert pill.font_px == theme.NAME_FONT_PX
    assert pill.runs[0].text == "BAJA UFMG"


def test_a_slightly_too_long_name_shrinks_but_is_not_cut():
    # Measure a name with no limit, then allow only 92 % of that width: it
    # must be drawn smaller (but not under 75 %) and still complete.
    name = "EQUIPE " + "X" * 10
    full = layout_name_pill("42", team_runs(name), max_width=10_000, scale=1.0)
    narrower = full.width * 0.92
    pill = layout_name_pill("42", team_runs(name), max_width=narrower, scale=1.0)
    assert theme.NAME_FONT_PX * 0.75 <= pill.font_px < theme.NAME_FONT_PX
    assert pill.runs[0].text == name
    assert pill.width <= narrower


def test_a_far_too_long_name_shrinks_to_75_percent_then_ends_with_an_ellipsis():
    name = "EQUIPE DE BAJA COM UM NOME ABSURDAMENTE LONGO " * 4
    pill = layout_name_pill("42", team_runs(name), max_width=1300, scale=1.0)
    assert pill.font_px == theme.NAME_FONT_PX * 0.75
    assert pill.runs[-1].text.endswith(ELLIPSIS)
    # Never wider than --lower-third-max-width.
    assert pill.width <= 1300


# --- The whole picture against the approved reference ---


def test_the_overlay_matches_the_golden_image(tmp_path):
    # Car 1 as synced from the results API (team short name, prova,
    # passagem and school short name), at 1920x1080.
    rendered = render_golden(tmp_path / "overlay.png")
    golden = Image.open(GOLDEN_PATH)
    assert rendered.size == golden.size
    # Small tolerance: font rasterisers differ by a shade at glyph edges
    # between machines, but a moved pill, a wrong colour or a changed word
    # changes thousands of pixels.
    assert count_differing_pixels(rendered, golden) <= 200
