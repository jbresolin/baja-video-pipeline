"""Tests for worker.overlay_tokens: the overlay's look is read from BajaOvrLay's tokens.css.

Background for a beginner: BajaOvrLay (the on-air graphics) keeps every
colour and size of its look in one CSS file, tokens.css, as "custom
properties" like `--radius: 8px;`. The worker keeps a copy of that file
(worker/overlay_theme/tokens.css) and parses it, instead of typing the
numbers again in Python, so the clip overlay follows the on-air look when
the file is refreshed. These tests check the parser and that every token
the overlay needs is really in the vendored file.
"""

import hashlib
import re
from importlib import resources
from pathlib import Path

import pytest

from worker import overlay_tokens
from worker.overlay_tokens import (
    ThemeTokenError,
    colour,
    em,
    number,
    parse_tokens,
    pixels,
)

SAMPLE_CSS = """
:root {
  /* a comment with a fake --token: 1px; inside */
  --brand-blue: #386db4;
  --panel-base: rgb(20 25 48 / 0.84);
  --chip-car-background: var(--brand-blue);
  --radius: 8px;
  --stage: 1728px;
  --column: 420px;
  --gap: 8px;
  --max: calc(var(--stage) - var(--column) - var(--gap));
  --tracking: 0.06em;
  --weight: 400;
  --font: 'Bebas Neue', sans-serif;
}
"""


def test_declarations_are_read_and_comments_ignored():
    tokens = parse_tokens(SAMPLE_CSS)
    assert tokens["--radius"] == "8px"
    # The "--token" written inside a comment must not become a token.
    assert "--token" not in tokens


def test_hex_and_rgb_colours_become_rgba_tuples():
    tokens = parse_tokens(SAMPLE_CSS)
    assert colour(tokens, "--brand-blue") == (0x38, 0x6D, 0xB4, 255)
    # 0.84 opacity → alpha 214 of 255.
    assert colour(tokens, "--panel-base") == (20, 25, 48, 214)


def test_var_references_are_followed():
    tokens = parse_tokens(SAMPLE_CSS)
    # --chip-car-background is "var(--brand-blue)": it must resolve to the blue.
    assert colour(tokens, "--chip-car-background") == (0x38, 0x6D, 0xB4, 255)


def test_calc_of_pixels_is_computed():
    tokens = parse_tokens(SAMPLE_CSS)
    # 1728 − 420 − 8, the way BajaOvrLay computes --lower-third-max-width.
    assert pixels(tokens, "--max") == 1300


def test_em_and_plain_numbers():
    tokens = parse_tokens(SAMPLE_CSS)
    assert em(tokens, "--tracking") == 0.06
    assert number(tokens, "--weight") == 400


def test_a_missing_token_or_an_unknown_form_is_an_error_not_a_guess():
    tokens = parse_tokens(SAMPLE_CSS)
    # A token that is not in the file: the overlay cannot be drawn right.
    with pytest.raises(ThemeTokenError):
        pixels(tokens, "--not-there")
    # A font list is not a length: refusing it beats drawing garbage.
    with pytest.raises(ThemeTokenError):
        pixels(tokens, "--font")
    with pytest.raises(ThemeTokenError):
        colour(tokens, "--radius")


def test_every_token_the_overlay_uses_is_in_the_vendored_file():
    # Collect every "--name" written in overlay_tokens.py, i.e. every token
    # the overlay reads, and check each exists in the vendored tokens.css.
    source = Path(overlay_tokens.__file__).read_text(encoding="utf-8")
    used = set(re.findall(r'"(--[a-z0-9-]+)"', source))
    assert len(used) >= 20  # sanity: the regex found the list
    vendored = overlay_tokens.load_vendored_tokens()
    missing = sorted(name for name in used if name not in vendored)
    assert missing == []


def test_the_constants_match_the_lower_third_of_the_brief():
    # The numbers the LIVESTACK-38 brief quotes from tokens.css at the
    # reference commit; if a refresh changes them, this test says which.
    assert overlay_tokens.LEFT_PX == 96
    assert overlay_tokens.STAGE_HEIGHT_PX - overlay_tokens.BOTTOM_OFFSET_PX == 940
    assert overlay_tokens.NAME_PILL_HEIGHT_PX == 64
    assert overlay_tokens.ROLE_PILL_HEIGHT_PX == 40
    assert overlay_tokens.PILL_RADIUS_PX == 8
    assert overlay_tokens.CHIP_RADIUS_PX == 4
    assert overlay_tokens.NAME_FONT_PX == 44
    assert overlay_tokens.ROLE_FONT_PX == 22
    assert overlay_tokens.GLYPH_NUDGE_PX == 3
    assert overlay_tokens.MAX_PILL_WIDTH_PX == 1300
    assert overlay_tokens.PANEL_COLOUR == (20, 25, 48, 214)
    assert overlay_tokens.TEXT_COLOUR == (0xE8, 0xEC, 0xF4, 255)
    assert overlay_tokens.MUTED_TEXT_COLOUR == (0xAA, 0xB4, 0xC8, 255)
    assert overlay_tokens.CHIP_BACKGROUND == (0x38, 0x6D, 0xB4, 255)
    assert overlay_tokens.CHIP_TEXT_COLOUR == (255, 255, 255, 255)
    assert overlay_tokens.FONT_WEIGHT == 400


def test_vendored_files_match_the_hashes_recorded_in_origin_md():
    # ORIGIN.md says where each vendored file came from and its SHA-256.
    # A file edited by hand (instead of refreshed with the tool) fails here.
    theme = resources.files("worker.overlay_theme")
    origin = theme.joinpath("ORIGIN.md").read_text(encoding="utf-8")
    rows = re.findall(r"\| `([^`]+)` \| `[^`]+` \| `[0-9a-f]{64}` \| `([0-9a-f]{64})` \|", origin)
    assert {name for name, _ in rows} == {
        "tokens.css",
        "BebasNeue-Regular.ttf",
        "BebasNeue-LICENSE.txt",
    }
    for name, recorded in rows:
        actual = hashlib.sha256(theme.joinpath(name).read_bytes()).hexdigest()
        assert actual == recorded, name
    assert "97d5673" in origin  # the reference commit of the brief
    # The font's licence travels with it (SIL Open Font License).
    assert "SIL Open Font License" in theme.joinpath("BebasNeue-LICENSE.txt").read_text()
