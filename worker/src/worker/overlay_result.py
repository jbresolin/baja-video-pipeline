"""The result pill: the passagem's official result under the lower third (LIVESTACK-47).

The orchestrator sends the line ready to draw (ProcessTask.pass_result_text,
e.g. "66 s · 4 cones → 86 s"); the worker never fetches results itself.
Here it becomes text runs, like the role pill's:

    66 S  ·  4 CONES  →  86 S
    words    muted "·"   the arrow, drawn as a shape (worker.overlay_arrow)

with the theme's part gap (--space-3) around each "·" and "→". The pill is
sized like a row of the theme's scoreboard (--board-row-height, --size-board),
so a penalty count stays readable at 1080p, and fits like the other pills:
shrink to 75 %, then cut with an ellipsis (never ending on a "·" or "→").
"""

import re

from worker import overlay_tokens as theme
from worker.overlay_arrow import ARROW
from worker.overlay_draw import PillLayout
from worker.overlay_text import TextRun, fit_runs, runs_width

SEPARATOR = "·"
# A "·" or "→" with the spaces around it; the brackets keep it in the split.
_SEPARATOR_OR_ARROW = re.compile(rf"\s*([{SEPARATOR}{ARROW}])\s*")


def result_runs(result_text: str) -> list[TextRun]:
    """The result line as runs: words in capitals, a muted "·", and "→" as an arrow run.

    Bebas Neue has no lowercase, so the words are set in capitals ("S",
    "SAÍDAS"). Empty pieces are skipped, a "·" or "→" right after another
    one is left out, and none is left at either end.
    """
    runs: list[TextRun] = []
    pieces = _SEPARATOR_OR_ARROW.split(result_text.strip().upper())
    for index, piece in enumerate(pieces):
        # re.split with a group alternates: words, separator, words, ...
        is_separator = index % 2 == 1
        if not is_separator:
            if piece.strip():
                runs.append(TextRun(piece.strip(), tracking_em=theme.RESULT_TRACKING_EM))
            continue
        if not runs or _is_separator(runs[-1]):
            continue
        if piece == ARROW:
            runs.append(TextRun(ARROW, gap_before=True, gap_after=True, arrow=True))
        else:
            runs.append(TextRun(SEPARATOR, muted=True, gap_before=True, gap_after=True))
    if runs and _is_separator(runs[-1]):
        runs.pop()
    return runs


def _is_separator(run: TextRun) -> bool:
    return run.gap_before and run.gap_after


def layout_result_pill(runs: list[TextRun], max_width: float, scale: float) -> PillLayout:
    """The result pill: --board-row-height tall, --size-board text, --space-4 each side."""
    height = theme.RESULT_PILL_HEIGHT_PX * scale
    padding = theme.RESULT_PILL_PADDING_PX * scale
    runs, font_px = fit_runs(runs, theme.RESULT_FONT_PX * scale, max_width - 2 * padding, scale)
    return PillLayout(
        2 * padding + runs_width(runs, font_px, scale), height, runs, padding, font_px
    )
