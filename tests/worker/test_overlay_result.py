"""Tests for the result pill (worker.overlay_result, LIVESTACK-47).

Background for a beginner: the clip overlay has two "pills" (rounded
boxes) at the bottom-left: the car and team, then the prova, passagem and
school. When the orchestrator sends the official result of the passagem
("66 s · 4 cones → 86 s"), a third pill shows it *under* the other two.
The other two must not move, so a clip with a result and one without look
the same apart from the new line.

How the tests find the pills: the PNG is transparent except the pills, so
walking down the pixel column x=100 shows each pill as a run of opaque
rows (pill_rows, from test_overlay.py).

The font (Bebas Neue) has no "→" character. Text drawn with a missing
character shows an empty box ("tofu"), so the arrow is drawn as a shape;
some tests below prove that is what ends up in the picture.
"""

from dataclasses import replace

import pytest
from PIL import Image, ImageChops, ImageDraw

from tests.worker.overlay_golden import (
    CAR_26_TEXT,
    RESULT_GOLDEN_CASES,
    RESULT_LONG,
    count_differing_pixels,
    render_golden,
)
from tests.worker.test_overlay import pill_right_edge, pill_rows, render
from worker import overlay_tokens as theme
from worker.overlay import render_lower_third
from worker.overlay_arrow import ARROW, arrow_width
from worker.overlay_result import layout_result_pill, result_runs
from worker.overlay_text import ELLIPSIS, TextRun, font, runs_width, truncate_runs

WITH_RESULT = replace(CAR_26_TEXT, pass_result="66 s · 4 cones → 86 s")


# --- What the pill says ---


def test_the_line_is_split_into_words_separators_and_an_arrow():
    runs = result_runs("58 s · 8 cones, 2 saídas de pista → 118 s")
    # Capitals (the face has no lowercase), accents kept ("SAÍDAS").
    assert [run.text for run in runs] == ["58 S", "·", "8 CONES, 2 SAÍDAS DE PISTA", "→", "118 S"]
    # The "·" is muted like the role pill's; the "→" is marked to be drawn
    # as a shape, in the normal text colour; nothing else is.
    assert [run.muted for run in runs] == [False, True, False, False, False]
    assert [run.arrow for run in runs] == [False, False, False, True, False]


def test_a_clean_run_and_a_decimal_comma_are_one_word():
    assert [run.text for run in result_runs("59 s")] == ["59 S"]
    assert [run.text for run in result_runs("59,4 s")] == ["59,4 S"]


# --- Where the pill is ---


def test_the_result_pill_sits_under_the_role_pill_and_the_others_do_not_move(tmp_path):
    # Setup: the same clip drawn twice, without and with a result line.
    without = render(tmp_path / "a", CAR_26_TEXT)
    with_result = render(tmp_path / "b", WITH_RESULT)

    # Without: the two pills of every clip, the role pill ending at y 939
    # (its bottom edge is y 940).
    assert pill_rows(without, 100) == [(833, 896), (900, 939)]
    # With: the same two, plus a 40 px pill starting 3 px (--row-gap) under
    # the role pill. It starts at the same left edge (x 96) as the others.
    assert pill_rows(with_result, 100) == [(833, 896), (900, 939), (943, 982)]
    assert pill_rows(with_result, 95) == []

    # Everything above the result pill is exactly the same picture, pixel
    # for pixel: adding a result moved or changed nothing up there.
    top_part = (0, 0, 1920, 941)
    difference = ImageChops.difference(without.crop(top_part), with_result.crop(top_part))
    assert difference.getbbox() is None


@pytest.mark.parametrize("missing", [None, "", "   "])
def test_no_result_pill_when_the_line_is_none_or_blank(tmp_path, missing):
    image = render(tmp_path, replace(CAR_26_TEXT, pass_result=missing))
    # Only the two usual pills: nothing below y 940.
    assert pill_rows(image, 100) == [(833, 896), (900, 939)]


def test_the_pill_is_sized_like_a_scoreboard_row():
    # The tokens: --board-row-height 40 px and --size-board 30 px, read from
    # the vendored tokens.css (if a theme refresh changes them, this says so).
    assert theme.RESULT_PILL_HEIGHT_PX == 40
    assert theme.RESULT_FONT_PX == 30
    # A short result is laid out at exactly those sizes.
    pill = layout_result_pill(result_runs("59 s"), max_width=1300, scale=1.0)
    assert pill.height == theme.RESULT_PILL_HEIGHT_PX
    assert pill.font_px == theme.RESULT_FONT_PX
    # --space-4 (16 px) on each side of the text.
    assert pill.text_left == theme.RESULT_PILL_PADDING_PX == 16


def test_at_720p_the_result_pill_scales_with_the_rest(tmp_path):
    image = render(tmp_path, WITH_RESULT, width=1280, height=720)
    # 1080 → 720 is ×2/3: three pills, the last one 40 × 2/3 ≈ 27 px tall.
    rows = pill_rows(image, 70)
    assert len(rows) == 3
    result_top, result_bottom = rows[2]
    assert abs((result_bottom - result_top + 1) - 40 * 2 / 3) <= 1


def test_a_narrow_frame_keeps_the_result_pill_inside_it(tmp_path):
    # A 608 px wide frame (an upright phone before LIVESTACK-50): the long
    # result is cut so its pill ends before the right safe edge (608 − 96).
    image = render(tmp_path, replace(CAR_26_TEXT, pass_result=RESULT_LONG), width=608)
    assert pill_right_edge(image, 960) <= 608 - 96


# --- The arrow is a drawn shape, not a missing-character box ---


def test_the_font_really_has_no_arrow():
    # Why the shape is needed: the font draws "→" exactly like a character
    # it certainly does not have (a private-use code point), i.e. as its
    # "missing glyph" box.
    def drawn(character: str) -> Image.Image:
        picture = Image.new("L", (40, 40), 0)
        ImageDraw.Draw(picture).text((5, 35), character, font=font(30), fill=255, anchor="ls")
        return picture

    arrow_as_text, unknown = drawn(ARROW), drawn("")
    assert arrow_as_text.getbbox() is not None  # something (the box) is drawn...
    assert ImageChops.difference(arrow_as_text, unknown).getbbox() is None  # ...the same


def ink_box(image: Image.Image, box: tuple[int, int, int, int]) -> tuple[int, int, int, int] | None:
    """The bounding box (inside `box`) of pixels much brighter than the slate pill: the ink."""
    area = image.crop(box).convert("L").point(lambda value: 255 if value > 150 else 0)
    return area.getbbox()


def test_the_arrow_is_drawn_as_a_flat_arrow_shape(tmp_path):
    # Setup: draw the pill "1 → 2" alone, and work out where the arrow sits:
    # after the padding, the "1", and one part gap.
    runs = result_runs("1 → 2")
    image = render_lower_third(
        1920, 1080, chip_text=None, name_runs=[], role_runs=[], result_runs=runs
    )
    on_black = Image.new("RGBA", image.size, (0, 0, 0, 255))
    on_black.alpha_composite(image)
    arrow_left = 96 + theme.RESULT_PILL_PADDING_PX + runs_width(runs[:1], 30, 1.0)
    arrow_left += theme.ROLE_PART_GAP_PX
    arrow_box = (int(arrow_left), 943, int(arrow_left + arrow_width(30)) + 1, 983)

    # The ink found there is the arrow: about as wide as arrow_width says,
    # and flat. A missing-glyph box would be a tall outline as high as the
    # capitals (21 px at 30 px): the arrow is under 12 px tall.
    left, top, right, bottom = ink_box(on_black, arrow_box)
    assert right - left >= arrow_width(30) - 2
    assert bottom - top <= 12

    # Its middle row is solid from end to end: the shaft (a box outline
    # would be ink only at its two sides).
    middle_y = 943 + (top + bottom) // 2
    shaft = [
        on_black.getpixel((x, middle_y))[0] > 150
        for x in range(arrow_box[0] + left + 1, arrow_box[0] + right - 1)
    ]
    assert all(shaft)


def test_drawing_the_arrow_as_text_would_look_different(tmp_path):
    # The same line with "→" as an ordinary character (no arrow flag): what
    # the overlay would show without the shape. The two pictures differ, so
    # the golden images below would catch a return of the tofu box.
    shape = render_lower_third(
        1920, 1080, chip_text=None, name_runs=[], role_runs=[], result_runs=result_runs("1 → 2")
    )
    as_text = render_lower_third(
        1920, 1080, chip_text=None, name_runs=[], role_runs=[], result_runs=[TextRun("1 → 2")]
    )
    assert count_differing_pixels(shape, as_text) > 50


# --- Long results: shrink first, then cut ---


def test_the_long_golden_line_shrinks_but_is_not_cut():
    # The long golden case is too wide at 30 px for the 1300 px maximum,
    # so it is drawn smaller (not under 75 %), but still complete.
    pill = layout_result_pill(result_runs(RESULT_LONG), max_width=1300, scale=1.0)
    assert theme.RESULT_FONT_PX * 0.75 <= pill.font_px < theme.RESULT_FONT_PX
    assert [run.text for run in pill.runs] == [run.text for run in result_runs(RESULT_LONG)]
    assert pill.width <= 1300


def test_a_far_too_long_line_shrinks_to_75_percent_then_ends_with_an_ellipsis():
    # Three times the long line cannot fit even at 75 %: it is cut.
    pill = layout_result_pill(result_runs(" · ".join([RESULT_LONG] * 3)), 1300, 1.0)
    assert pill.font_px == theme.RESULT_FONT_PX * 0.75
    assert pill.runs[-1].text.endswith(ELLIPSIS)
    assert not pill.runs[-1].arrow
    assert pill.width <= 1300


def test_a_cut_never_ends_on_the_arrow():
    # Setup: room for exactly "66 S · 4 CONES →" and an ellipsis, so the
    # naive cut would leave the arrow dangling at the end ("4 CONES → …").
    runs = result_runs("66 s · 4 cones → 86 s")
    font_px = 22.5
    room = runs_width(runs[:4], font_px, 1.0) + font(font_px).getlength(ELLIPSIS)

    cut = truncate_runs(runs, font_px, room, 1.0)

    # The arrow (and the result it pointed to) went; the words before it
    # end with the ellipsis instead: "66 S · 4 CONES…".
    assert not any(run.arrow for run in cut)
    assert cut[-1].text == "4 CONES" + ELLIPSIS


# --- The four pictures against the approved references ---


@pytest.mark.parametrize("case", sorted(RESULT_GOLDEN_CASES))
def test_each_result_case_matches_its_golden_image(tmp_path, case):
    # Car 26 (clean run, cones), car 2 (cones and track exits) and a long
    # made-up line, each a whole overlay at 1920x1080. Each picture was
    # checked by eye: the arrow, the "·", the accents and the shrunk line.
    text, golden_path = RESULT_GOLDEN_CASES[case]

    rendered = render_golden(tmp_path / "overlay.png", text)

    golden = Image.open(golden_path)
    assert rendered.size == golden.size
    # Same tolerance as the other golden tests: font rasterisers differ by
    # a shade at glyph edges, a wrong word changes thousands of pixels.
    assert count_differing_pixels(rendered, golden) <= 200
