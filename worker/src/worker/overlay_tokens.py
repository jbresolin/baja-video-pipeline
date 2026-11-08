"""The clip overlay's theme: named constants read from BajaOvrLay's tokens.css.

The overlay must look like BajaOvrLay's lower third (theme Ardósia), and
that look is defined by CSS custom properties in its tokens.css. A copy of
that file is vendored in worker/overlay_theme/ (see ORIGIN.md there). This
module parses it and exposes the values the overlay draws with as plain
Python constants (sizes in pixels at 1920x1080, colours as RGBA tuples).
Nothing here is retyped from the CSS: refreshing the look is copying a new
tokens.css (tools/refresh_overlay_theme.py) and rerunning the tests.

Only the CSS forms tokens.css actually uses are understood: `12px`, `0.04em`,
plain numbers, `#rrggbb`, `rgb(r g b / a)`, `var(--other)` and `calc()` of
pixel values added and subtracted. Anything else raises ThemeTokenError,
so a token that changed form fails the tests instead of drawing garbage.
"""

import hashlib
import re
from importlib import resources

THEME_PACKAGE = "worker.overlay_theme"
TOKENS_FILE = "tokens.css"
FONT_FILE = "BebasNeue-Regular.ttf"

_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_DECLARATION = re.compile(r"(--[a-z0-9-]+)\s*:\s*([^;]+);")
_VAR = re.compile(r"^var\((--[a-z0-9-]+)\)$")
_CALC = re.compile(r"^calc\((.*)\)$")
_LENGTH = re.compile(r"^(-?\d+(?:\.\d+)?)(px|em)?$")
_HEX = re.compile(r"^#([0-9a-fA-F]{6})$")
_RGB = re.compile(r"^rgb\(\s*(\d+)\s+(\d+)\s+(\d+)\s*(?:/\s*([\d.]+)\s*)?\)$")

Rgba = tuple[int, int, int, int]


class ThemeTokenError(ValueError):
    """A token is missing, or written in a CSS form this parser does not know."""


def parse_tokens(css_text: str) -> dict[str, str]:
    """Every `--name: value;` declaration in the file, comments removed, values stripped."""
    without_comments = _COMMENT.sub("", css_text)
    return {name: value.strip() for name, value in _DECLARATION.findall(without_comments)}


def resolve(tokens: dict[str, str], name: str) -> str:
    """The token's value with `var(--x)` references followed (also inside calc())."""
    if name not in tokens:
        raise ThemeTokenError(f"tokens.css has no {name}")
    value = tokens[name]
    reference = _VAR.match(value)
    if reference:
        return resolve(tokens, reference.group(1))
    return re.sub(r"var\((--[a-z0-9-]+)\)", lambda m: resolve(tokens, m.group(1)), value)


def pixels(tokens: dict[str, str], name: str) -> float:
    """A length token in pixels: `8px`, or calc() of px values joined by + and -."""
    value = resolve(tokens, name)
    calc = _CALC.match(value)
    if calc:
        # "1728px - 420px - 8px": numbers with their sign, read left to right.
        terms = re.findall(r"([+-]?)\s*(\d+(?:\.\d+)?)px", calc.group(1).replace(" ", ""))
        if not terms:
            raise ThemeTokenError(f"{name}: cannot read calc() {value!r}")
        return sum(float(number) * (-1 if sign == "-" else 1) for sign, number in terms)
    length = _LENGTH.match(value)
    if not length or length.group(2) not in ("px", None):
        raise ThemeTokenError(f"{name}: expected a px length, got {value!r}")
    return float(length.group(1))


def em(tokens: dict[str, str], name: str) -> float:
    """A letter-spacing token in em (a fraction of the font size), e.g. 0.06."""
    length = _LENGTH.match(resolve(tokens, name))
    if not length or length.group(2) != "em":
        raise ThemeTokenError(f"{name}: expected an em length, got {tokens[name]!r}")
    return float(length.group(1))


def number(tokens: dict[str, str], name: str) -> float:
    """A unitless number token, e.g. a font weight of 400."""
    length = _LENGTH.match(resolve(tokens, name))
    if not length or length.group(2) is not None:
        raise ThemeTokenError(f"{name}: expected a plain number, got {tokens[name]!r}")
    return float(length.group(1))


def colour(tokens: dict[str, str], name: str) -> Rgba:
    """A colour token as (red, green, blue, alpha), each 0..255."""
    value = resolve(tokens, name)
    hex_match = _HEX.match(value)
    if hex_match:
        digits = hex_match.group(1)
        return (int(digits[0:2], 16), int(digits[2:4], 16), int(digits[4:6], 16), 255)
    rgb_match = _RGB.match(value)
    if rgb_match:
        red, green, blue, alpha = rgb_match.groups()
        opacity = float(alpha) if alpha is not None else 1.0
        return (int(red), int(green), int(blue), round(opacity * 255))
    raise ThemeTokenError(f"{name}: expected #rrggbb or rgb(r g b / a), got {value!r}")


def load_vendored_tokens() -> dict[str, str]:
    css_text = resources.files(THEME_PACKAGE).joinpath(TOKENS_FILE).read_text(encoding="utf-8")
    return parse_tokens(css_text)


def vendored_theme_version() -> str:
    """SHA-256 of the vendored tokens.css: which look this worker draws (LIVESTACK-43).

    Reported with every clip's render inputs, so a clip drawn before a
    theme refresh can be told apart from one drawn after it.
    """
    css_bytes = resources.files(THEME_PACKAGE).joinpath(TOKENS_FILE).read_bytes()
    return hashlib.sha256(css_bytes).hexdigest()


def font_path() -> str:
    """Filesystem path of the vendored Bebas Neue TTF (Pillow wants a path)."""
    return str(resources.files(THEME_PACKAGE).joinpath(FONT_FILE))


_TOKENS = load_vendored_tokens()
THEME_VERSION = vendored_theme_version()

# --- Where the lower third sits on the 1920x1080 stage ---
STAGE_HEIGHT_PX = 1080
LEFT_PX = pixels(_TOKENS, "--bug-left")  # 96: the title-safe left edge
BOTTOM_OFFSET_PX = pixels(_TOKENS, "--lower-third-bottom")  # 140: bottom pill ends at y 940
PILL_GAP_PX = pixels(_TOKENS, "--row-gap")  # between the name pill and the role pill
MAX_PILL_WIDTH_PX = pixels(_TOKENS, "--lower-third-max-width")  # 1300

# --- The pills ---
PANEL_COLOUR = colour(_TOKENS, "--panel-base")
PILL_RADIUS_PX = pixels(_TOKENS, "--radius")
NAME_PILL_HEIGHT_PX = pixels(_TOKENS, "--name-height")
NAME_PILL_PADDING_PX = pixels(_TOKENS, "--space-5")
ROLE_PILL_HEIGHT_PX = pixels(_TOKENS, "--role-height")
ROLE_PILL_PADDING_PX = pixels(_TOKENS, "--space-4")
ROLE_PART_GAP_PX = pixels(_TOKENS, "--space-3")  # around the "·" between role parts
# The result pill (LIVESTACK-47) is sized like a row of the theme's
# scoreboard, so a penalty count stays readable at 1080p.
RESULT_PILL_HEIGHT_PX = pixels(_TOKENS, "--board-row-height")  # 40
RESULT_PILL_PADDING_PX = pixels(_TOKENS, "--space-4")

# --- Text ---
TEXT_COLOUR = colour(_TOKENS, "--text")
MUTED_TEXT_COLOUR = colour(_TOKENS, "--text-muted")
NAME_FONT_PX = pixels(_TOKENS, "--size-name")  # 44
ROLE_FONT_PX = pixels(_TOKENS, "--size-role")  # 22
RESULT_FONT_PX = pixels(_TOKENS, "--size-board")  # 30
NAME_TRACKING_EM = em(_TOKENS, "--tracking-row")
ROLE_TRACKING_EM = em(_TOKENS, "--tracking-caption")
RESULT_TRACKING_EM = em(_TOKENS, "--tracking-row")
FONT_WEIGHT = number(_TOKENS, "--weight-display")  # 400: the face's only weight

# --- The car chip inside the name pill ---
CHIP_BACKGROUND = colour(_TOKENS, "--chip-car-background")
CHIP_TEXT_COLOUR = colour(_TOKENS, "--chip-car-text")
CHIP_RADIUS_PX = pixels(_TOKENS, "--radius-chip")
CHIP_FONT_PX = pixels(_TOKENS, "--size-chip-large")  # 40
CHIP_INSET_PX = pixels(_TOKENS, "--space-2")  # chip to the pill's edges
CHIP_TEXT_PADDING_PX = pixels(_TOKENS, "--space-3")  # digits to the chip's sides
CHIP_NAME_GAP_PX = pixels(_TOKENS, "--space-4")
GLYPH_NUDGE_PX = pixels(_TOKENS, "--glyph-nudge")  # digits sit high; push them down

# --- The running clock (LIVESTACK-53): bigger than the pills' text, with its unit ---
# The theme's on-air clock: --size-time digits in a pill as tall as the
# on-air strip, with the name pill's padding. The clip's clock is this
# grown in proportion to João's 80 px digits (worker.overlay_clock).
ON_AIR_CLOCK_FONT_PX = pixels(_TOKENS, "--size-time")  # 56
ON_AIR_CLOCK_HEIGHT_PX = pixels(_TOKENS, "--strip-height")  # 72
ON_AIR_CLOCK_PADDING_PX = pixels(_TOKENS, "--space-5")  # 20, like the name pill
# Top right (LIVESTACK-53): its right edge as far from the frame's right as
# the lower third is from the left (--bug-left, so the corners mirror), its
# top at the theme's title-safe top: nearer the top than the lower third is
# to the bottom, as João asked, and well inside action-safe (≥ 3.5 %).
CLOCK_RIGHT_PX = LEFT_PX  # 96
CLOCK_TOP_PX = pixels(_TOKENS, "--stage-safe-top")  # 54: 5 % of 1080
