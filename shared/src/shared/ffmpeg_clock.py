"""The running clock in the final render, as ffmpeg filters (LIVESTACK-48).

The overlay was one still PNG (design §22); the clock is its first moving
part. It is two steps on top of the overlay, both switched on at the
largada (timeline `enable`) and off before it:

* the clock's pill, a small PNG the worker draws with Pillow like the
  other pills (worker.overlay_clock), laid on with `overlay`;
* the digits, written by ffmpeg's `drawtext` with the very font file the
  pills use, in the pills' text colour, right-aligned in the pill (Bebas
  Neue's digits all have the same width, so the text never jumps), its
  baseline where Pillow puts the pills' baseline. The text is an
  expression of the frame's own time `t`, evaluated for every frame.

What the digits show follows shared.pass_clock exactly: elapsed
microseconds (each time rounded half up on its own), counted in clock
units, rounded down, never past the official time. ffmpeg's `t` is the
time since the cut-in (the render seeks there), so raw time = cut-in + t.

Why drawtext and not images moved per frame: an earlier version showed
each digit from a Pillow-drawn strip, which needs the frame region cut
out, five overlays and the region put back; with ffmpeg 6.1 that made a
4K render about 25 % slower. These two filters cost nothing measurable.

Escaping: the filter graph is one text. Each option value here is put in
single quotes (so ",", ";" and "[" are plain characters), and inside it
":" and "\\" are escaped with a backslash, as ffmpeg's option parser
expects. A font path with a single quote cannot be written and is refused.
"""

from dataclasses import dataclass
from pathlib import Path

from shared.pass_clock import MICROSECONDS_PER_SECOND, microseconds, units_per_second


class ClockFilterError(ValueError):
    """Something cannot be written into a filter graph (a quote in the font path)."""


def quoted_option(value: str) -> str:
    """`value` as one option value of a filter, quoted for the filter graph."""
    if "'" in value:
        raise ClockFilterError(f"a single quote cannot be written in a filter graph: {value!r}")
    return "'" + value.replace("\\", "\\\\").replace(":", "\\:") + "'"


@dataclass(frozen=True)
class ClockOverlay:
    """Everything the render needs to draw the clock (worker.overlay_clock makes it)."""

    # The pill (no text), and where its top-left corner goes in the frame.
    pill_png: Path
    pill_position: tuple[int, int]
    # The digits: font file and size, colour ("0xRRGGBBAA"), where the
    # text ends on the right and where its baseline is, in frame pixels.
    font_path: Path
    font_px: int
    text_colour: str
    text_right: float
    baseline: int
    # Raw-video seconds: the cut-in (where the clip's t = 0) and the largada.
    cut_in_s: float
    largada_s: float
    official_units: int
    decimals: int

    def elapsed_us_expression(self) -> str:
        """Microseconds since the largada at the frame's time `t`, as an ffmpeg expression."""
        offset = microseconds(self.cut_in_s) - microseconds(self.largada_s)
        return f"(round(t*{MICROSECONDS_PER_SECOND}){offset:+d})"

    def units_expression(self) -> str:
        """The clock's count at `t`: min(floor(elapsed · units/s), official)."""
        per_second = units_per_second(self.decimals)
        elapsed = self.elapsed_us_expression()
        return f"min(floor({elapsed}*{per_second}/{MICROSECONDS_PER_SECOND}),{self.official_units})"

    def shown_expression(self) -> str:
        """True (1) from the largada on."""
        return f"gte({self.elapsed_us_expression()},0)"

    def text_expansion(self) -> str:
        """drawtext's text: the whole seconds, then "," and the decimals zero-padded.

        "%{eif:expr:d}" writes an expression as a whole number, and
        "%{eif:expr:d:2}" pads it with zeros to 2 digits ("0,05").
        """
        units = self.units_expression()
        per_second = units_per_second(self.decimals)
        whole = f"%{{eif:floor({units}/{per_second}):d}}"
        if self.decimals == 0:
            return whole
        return f"{whole},%{{eif:mod({units},{per_second}):d:{self.decimals}}}"


def drawtext_filter(clock: ClockOverlay, text: str, enable: str | None) -> str:
    """drawtext writing `text` (a literal, or a text expansion) where the clock's digits go."""
    options = [
        f"fontfile={quoted_option(clock.font_path.as_posix())}",
        f"fontsize={clock.font_px}",
        f"fontcolor={clock.text_colour}",
        # x = the right edge minus the text's width: right-aligned.
        f"x={quoted_option(f'{clock.text_right:.3f}-text_w')}",
        "y_align=baseline",
        f"y={clock.baseline}",
        f"text={quoted_option(text)}",
    ]
    if enable is not None:
        options.append(f"enable={quoted_option(enable)}")
    return "drawtext=" + ":".join(options)


def clock_filter(clock: ClockOverlay, *, source: str, pill_input: int) -> str:
    """The filter_complex entry drawing the clock onto `[source]`.

    `pill_input` is the input index of clock.pill_png. The entry has no
    output label, so the caller can chain the pixel format onto it.
    """
    shown = clock.shown_expression()
    x, y = clock.pill_position
    pill = f"[{source}][{pill_input}:v]overlay=x={x}:y={y}:enable={quoted_option(shown)}"
    return f"{pill},{drawtext_filter(clock, clock.text_expansion(), shown)}"
