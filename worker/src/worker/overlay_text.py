"""Measuring and cutting the overlay's text (worker.overlay draws it).

A pill's text is a list of TextRun: pieces drawn one after the other, each
in the normal or the muted colour, with its own letter-spacing, and with
the theme's part gap (--space-3) around the "·" separators. The functions
here answer "how wide is this at that font size?" and "cut it to fit".
"""

from dataclasses import dataclass, replace
from functools import lru_cache

from PIL import ImageFont

from worker import overlay_tokens as theme

ELLIPSIS = "…"


@dataclass(frozen=True)
class TextRun:
    text: str
    muted: bool = False
    # Letter-spacing as a fraction of the font size (CSS `letter-spacing: 0.06em`).
    tracking_em: float = 0.0
    # The theme's part gap before/after this run (around a "·" separator).
    gap_before: bool = False
    gap_after: bool = False


@lru_cache(maxsize=64)
def font(size_px: float) -> ImageFont.FreeTypeFont:
    """The vendored Bebas Neue at `size_px` (fractional sizes allowed)."""
    return ImageFont.truetype(theme.font_path(), size_px)


def run_width(run: TextRun, font_px: float, scale: float) -> float:
    """Width of one run: its glyphs, its letter-spacing after each character, its gaps."""
    width = font(font_px).getlength(run.text) + len(run.text) * run.tracking_em * font_px
    gap = theme.ROLE_PART_GAP_PX * scale
    return width + gap * (int(run.gap_before) + int(run.gap_after))


def runs_width(runs: list[TextRun], font_px: float, scale: float) -> float:
    return sum(run_width(run, font_px, scale) for run in runs)


def truncate_runs(
    runs: list[TextRun], font_px: float, available: float, scale: float, ellipsis: str = ELLIPSIS
) -> list[TextRun]:
    """The runs cut from the end, with `ellipsis` added, so they fit in `available` pixels.

    Characters are removed from the last run first; a run that loses all its
    text is dropped, and so is a separator left dangling at the end. The
    ellipsis takes the colour of the run it ends. At worst only the
    ellipsis is left.
    """
    kept = list(runs)
    while kept:
        last = kept[-1]
        if last.gap_before and last.gap_after:
            # Never end on a "·" separator.
            kept.pop()
            continue
        candidate = [*kept[:-1], replace(last, text=last.text.rstrip() + ellipsis)]
        if runs_width(candidate, font_px, scale) <= available:
            return candidate
        if len(last.text) <= 1:
            kept.pop()
        else:
            kept[-1] = replace(last, text=last.text[:-1])
    return [TextRun(ellipsis)]
